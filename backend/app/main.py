"""FastAPI application entrypoint."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from .api.routes import router as api_router
from .api.websocket import manager
from .config import get_settings
from .services import get_database


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"starting CleanND in {settings.environment}")
    db = get_database()
    db.init()
    # start the websocket feed loop in background
    push_task = asyncio.create_task(_ws_pusher())
    # start the mock auto-seed (kiosk mode) if enabled
    autoseed_task = None
    if settings.mock_auto_seed_enabled:
        autoseed_task = asyncio.create_task(_mock_autoseed_task())
    background_tasks = [t for t in (push_task, autoseed_task) if t is not None]
    try:
        yield
    finally:
        for t in background_tasks:
            t.cancel()
        for t in background_tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        logger.info("CleanND shut down cleanly")


async def _ws_pusher() -> None:
    from .api.websocket import feed_push_loop

    await feed_push_loop()


async def _mock_autoseed_task() -> None:
    """Kiosk-mode auto-seeder.

    Periodically checks how many surfaced items are in the feed; if below
    `mock_auto_seed_min_feed_size`, runs a small mock ingest to top it up.
    Keeps the dashboard populated without manual clicks. Disable via
    MOCK_AUTO_SEED_ENABLED=false (e.g. in tests).
    """
    from sqlalchemy import func, select

    from .api.routes import _run_mock_ingest
    from .models.db_models import TweetORM
    from .models.schemas import CredibilityLevel

    s = settings
    # initial delay so the API finishes booting before we start hitting the DB
    await asyncio.sleep(s.mock_auto_seed_initial_delay_seconds)

    # Match the feed endpoint's filter so the autoseed count reflects what's
    # actually visible on the dashboard.
    level_order = {
        CredibilityLevel.UNVERIFIED: 0.0,
        CredibilityLevel.LOW: 0.2,
        CredibilityLevel.MEDIUM: s.credibility_medium_threshold,
        CredibilityLevel.HIGH: s.credibility_high_threshold,
    }
    min_credibility = level_order.get(
        CredibilityLevel(s.surface_min_credibility), s.credibility_medium_threshold
    )

    logger.info(
        f"[autoseed] enabled — interval={s.mock_auto_seed_check_interval_seconds}s "
        f"min_feed={s.mock_auto_seed_min_feed_size} batch={s.mock_auto_seed_batch_size} "
        f"min_credibility={min_credibility}"
    )
    while True:
        try:
            database = get_database()
            with database.session() as sess:
                surfaced = (
                    sess.execute(
                        select(func.count(TweetORM.id))
                        .where(TweetORM.passed_all_stages.is_(True))
                        .where(TweetORM.credibility_score >= min_credibility)
                    ).scalar()
                    or 0
                )
            if surfaced < s.mock_auto_seed_min_feed_size:
                logger.info(
                    f"[autoseed] feed has {surfaced} items (< {s.mock_auto_seed_min_feed_size}), "
                    f"running mock ingest n={s.mock_auto_seed_batch_size}"
                )
                # run in a thread so we don't block the event loop
                await asyncio.to_thread(
                    _run_mock_ingest, n=s.mock_auto_seed_batch_size, seed=None
                )
            else:
                logger.debug(f"[autoseed] feed healthy ({surfaced} items) — skipping")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover — defensive
            logger.warning(f"[autoseed] tick failed: {e}")
        await asyncio.sleep(s.mock_auto_seed_check_interval_seconds)


app = FastAPI(
    title="CleanND",
    description="Cleaned, credible news dashboard",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(api_router, prefix="/api")


# ---------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # keep alive - we don't expect inbound messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ---------------------------------------------------------------------
# Static frontend (when built)
# ---------------------------------------------------------------------

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "out"
if FRONTEND_DIST.exists():
    app.mount(
        "/_next",
        StaticFiles(directory=str(FRONTEND_DIST / "_next")),
        name="next",
    )
    app.mount(
        "/static",
        StaticFiles(directory=str(FRONTEND_DIST)),
        name="static",
    )

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(FRONTEND_DIST / "index.html"))
else:
    @app.get("/")
    async def index() -> dict:
        return {
            "message": "CleanND API is running",
            "docs": "/docs",
            "note": "Frontend not built. Run `cd frontend && npm run build` for static hosting, or use the API directly.",
        }