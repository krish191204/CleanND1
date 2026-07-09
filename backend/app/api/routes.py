"""REST routes for the dashboard, review queue, and pipeline control."""
from __future__ import annotations

import asyncio
from datetime import datetime
from enum import Enum
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from ..config import get_settings
from ..models.schemas import (
    CredibilityLevel,
    NewsCard,
    PipelineStats,
    ReviewItem,
    ReviewLabel,
    ScoredTweet,
    TopicDetailResponse,
    TopicListResponse,
    TopicSummary,
)
from ..pipeline import Pipeline
from ..services import Database, ReviewQueue, TwitterClient, get_database, quick_search
from ..services.cards import to_card


router = APIRouter()
_settings = get_settings()


# ---------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------

def db() -> Database:
    return get_database()


def review_queue() -> ReviewQueue:
    return ReviewQueue(db())


# ---------------------------------------------------------------------
# Schemas for the API
# ---------------------------------------------------------------------

class IngestRequest(BaseModel):
    query: Optional[str] = Field(None, description="Advanced search query string")
    beat: Optional[str] = Field(None, description="Named beat from NEWS_QUERIES (e.g. 'breaking', 'tech')")
    max_results: int = 50
    poll: bool = False
    poll_seconds: int = 60


class IngestResponse(BaseModel):
    job_id: str
    query: str
    fetched: int
    surfaced: int
    review_queue: int
    stats: PipelineStats


class LabelRequest(BaseModel):
    label: ReviewLabel
    category: Optional[str] = None
    notes: Optional[str] = None
    labeler_id: Optional[str] = "anonymous"


class FeedbackSignal(str, Enum):
    UP = "up"
    DOWN = "down"


class FeedbackRequest(BaseModel):
    tweet_id: str
    signal: FeedbackSignal
    category: Optional[str] = None
    notes: Optional[str] = None
    user_id: Optional[str] = "anonymous"


class CardListResponse(BaseModel):
    items: list[NewsCard]
    next_cursor: Optional[int] = None
    total: int


# ---------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------

@router.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "time": datetime.utcnow().isoformat(),
        "env": _settings.environment,
    }


# ---------------------------------------------------------------------
# Dashboard feed
# ---------------------------------------------------------------------

@router.get("/feed", response_model=CardListResponse)
async def feed(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    min_credibility: Optional[float] = Query(None, ge=0.0, le=1.0),
    min_level: Optional[str] = Query(None, description="high | medium | low | unverified"),
    handle: Optional[str] = None,
    human_verified: Optional[bool] = None,
    include_mock: bool = Query(False, description="Include mock-generated tweets (default off)"),
    _: Database = Depends(db),
) -> CardListResponse:
    """Surface feed.

    Defaults to SURFACE_MIN_CREDIBILITY (high) — cleanest feed.
    Pass `min_level=medium` to opt in to more items, or `min_credibility=0` for all.
    Mock data is filtered out by default — pass `?include_mock=true` to see
    the kiosk auto-seeder's synthetic tweets (for design review).
    """
    database = db()
    settings = get_settings()
    if min_credibility is None:
        from ..models.schemas import CredibilityLevel
        if min_level is not None:
            level = CredibilityLevel(min_level)
        else:
            level = CredibilityLevel(settings.surface_min_credibility)
        level_order = {
            CredibilityLevel.UNVERIFIED: 0.0,
            CredibilityLevel.LOW: 0.2,
            CredibilityLevel.MEDIUM: settings.credibility_medium_threshold,
            CredibilityLevel.HIGH: settings.credibility_high_threshold,
        }
        min_credibility = level_order.get(level, settings.credibility_high_threshold)

    rows = database.get_surfaced(
        limit=limit,
        offset=offset,
        min_credibility=min_credibility,
        handle=handle,
        include_mock=include_mock,
    )
    cards = [to_card(_row_to_scored(r)) for r in rows]
    return CardListResponse(items=cards, next_cursor=offset + len(cards), total=len(cards))


@router.get("/feed/card/{tweet_id}", response_model=NewsCard)
async def get_card(tweet_id: str, _: Database = Depends(db)) -> NewsCard:
    """Single card with extra detail (used for share links / deep links)."""
    database = db()
    row = database.get_one(tweet_id)
    if row is None:
        raise HTTPException(404, "tweet not found")
    return to_card(_row_to_scored(row))


# ---------------------------------------------------------------------
# Topic endpoints (Layer B Addition 1)
# ---------------------------------------------------------------------

@router.get("/topics", response_model=TopicListResponse)
async def list_topics(
    limit: int = Query(50, ge=1, le=200),
    include_mock: bool = Query(False, description="Include mock-only topics (default off)"),
    _: Database = Depends(db),
) -> TopicListResponse:
    """Topic-grouped surface feed.

    Returns topic summaries (one row per cluster), newest activity first.
    Use `/api/topics/{id}/tweets` to drill into a specific topic.

    Topics whose only members are mock-data are filtered out by default
    (the live dashboard never shows fabricated clusters). Pass
    `?include_mock=true` to surface them.
    """
    database = db()
    rows = database.get_topics(limit=limit, include_mock=include_mock)
    items: list[TopicSummary] = []
    for r in rows:
        anchor = None
        if r.get("anchor_tweet_id"):
            anchor_row = database.get_one(r["anchor_tweet_id"])
            if anchor_row is not None:
                anchor = to_card(_row_to_scored(anchor_row))
        items.append(TopicSummary(
            id=r["id"],
            label=r["label"],
            anchor_tweet_id=r["anchor_tweet_id"],
            anchor=anchor,
            tweet_count=r["tweet_count"],
            first_seen_at=datetime.fromisoformat(r["first_seen_at"]) if r["first_seen_at"] else datetime.now(),
            last_activity_at=datetime.fromisoformat(r["last_activity_at"]) if r["last_activity_at"] else datetime.now(),
            tweet_type_breakdown=r["tweet_type_breakdown"] or {},
        ))
    return TopicListResponse(items=items, next_cursor=None, total=len(items))


@router.get("/topics/{topic_id}/tweets", response_model=TopicDetailResponse)
async def topic_detail(
    topic_id: str,
    tweet_type: Optional[str] = Query(
        None,
        description="Optional tweet_type filter (announcement|opinion|news_report|analysis)",
    ),
    include_mock: bool = Query(False, description="Include mock-generated tweets in this topic"),
    _: Database = Depends(db),
) -> TopicDetailResponse:
    """All tweets in a single topic, sorted by final_score desc.

    Use `?tweet_type=opinion` etc. to filter to a single tweet kind
    within the topic. Pass `?include_mock=true` to surface mock-data
    members (filtered out by default).
    """
    database = db()
    topic = database.get_topic(topic_id)
    if topic is None:
        raise HTTPException(404, "topic not found")

    anchor = None
    if topic.get("anchor_tweet_id"):
        anchor_row = database.get_one(topic["anchor_tweet_id"])
        if anchor_row is not None:
            anchor = to_card(_row_to_scored(anchor_row))

    rows = database.get_topic_tweets(
        topic_id, tweet_type=tweet_type, include_mock=include_mock
    )
    tweets = [to_card(_row_to_scored(r)) for r in rows]

    summary = TopicSummary(
        id=topic["id"],
        label=topic["label"],
        anchor_tweet_id=topic["anchor_tweet_id"],
        anchor=anchor,
        tweet_count=topic["tweet_count"],
        first_seen_at=datetime.fromisoformat(topic["first_seen_at"]) if topic["first_seen_at"] else datetime.now(),
        last_activity_at=datetime.fromisoformat(topic["last_activity_at"]) if topic["last_activity_at"] else datetime.now(),
        tweet_type_breakdown={},
    )
    return TopicDetailResponse(topic=summary, tweets=tweets)


# ---------------------------------------------------------------------
# Ingest / run pipeline
# ---------------------------------------------------------------------

@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    req: IngestRequest,
    background: BackgroundTasks,
    _: Database = Depends(db),
) -> IngestResponse:
    """Fetch tweets for a query and run them through the cleaning pipeline.

    `poll=true` will start a background loop that re-runs every `poll_seconds`
    and pushes the latest results into the dashboard feed.
    """
    if not req.beat and not req.query:
        raise HTTPException(400, "either `query` or `beat` must be provided")

    # Run the real-ingest core (async, since the twitter client is async).
    try:
        n_persisted = await _ingest_real_to_db(
            query_or_beat=req.query or req.beat,
            max_results=req.max_results,
        )
    except Exception as e:
        logger.warning(f"twitter api failed: {e}")
        raise HTTPException(502, f"twitter client error: {e}") from e

    query = req.query or _resolve_beat(req.beat)

    if req.poll:
        background.add_task(_poll_loop, query, req.poll_seconds, req.max_results)

    return IngestResponse(
        job_id=datetime.utcnow().isoformat(),
        query=query,
        fetched=n_persisted,  # rough — surfaced+demoted only
        surfaced=n_persisted,
        review_queue=0,
        stats=PipelineStats(),  # empty; full stats live in the DB log
    )


# ---------------------------------------------------------------------
# Real-ingest core (synchronous; called by both the HTTP endpoint and the
# background real_ingest task in app/main.py)
# ---------------------------------------------------------------------

def _resolve_beat(beat: str) -> str:
    """Map a beat name to its NEWS_QUERIES string. Falls back to the beat
    as a raw query if no preset matches."""
    from ..services.twitter_client import NEWS_QUERIES

    return NEWS_QUERIES.get(beat, beat)


async def _ingest_real_to_db(
    query_or_beat: str,
    max_results: int = 25,
    persist_budget: int | None = None,
    priority_bypass: bool = True,
) -> int:
    """Run one real-ingest pass: API call → pipeline → DB upsert.

    Async because the twitter client is async. Called by both the HTTP
    endpoint and the background real_ingest task in app/main.py.

    Issue 1: when `real_ingest_parallel_known_handle_query=True`, also
    run a parallel `from:OpenAI OR from:Anthropic OR ...` query with no
    min_faves so 0-5-minute-old breaking-news tweets from trusted
    sources still surface. Results are merged by id before the pipeline.

    Issue 5: `persist_budget` is a per-beat cap. When `priority_bypass=True`,
    tweets from known handles don't count against the cap (they're persisted
    unconditionally so a known-handle tweet isn't dropped because some
    other tweet filled the budget first).

    Returns the number of items persisted (surfaced + demoted). Raises
    TwitterAPIError on API failure so the caller can decide whether
    to back off.
    """
    from ..config import get_settings
    from ..services.known_handles import is_known_any

    s = get_settings()
    if persist_budget is None:
        persist_budget = s.real_ingest_max_persist_per_beat

    query = _resolve_beat(query_or_beat)

    # Issue 1: build parallel query from cached known-news handles
    parallel_query: str | None = None
    if s.real_ingest_parallel_known_handle_query:
        from ..services.known_handles import known_news_handles
        news_handles = sorted(known_news_handles())
        if news_handles:
            # Cap to a sensible size; ~50 handles keeps the query compact
            handles_clause = " OR ".join(f"from:{h}" for h in news_handles[:50])
            parallel_query = f"({handles_clause}) lang:en"

    client = TwitterClient()
    try:
        raw_main = await client.search_tweets(query, max_results=max_results)
        raw_parallel: list = []
        if parallel_query is not None:
            try:
                # brief QPS gap between the two calls
                import asyncio as _asyncio
                await _asyncio.sleep(s.real_ingest_query_delay_seconds)
                raw_parallel = await client.search_tweets(
                    parallel_query, max_results=max_results
                )
            except Exception as e:
                logger.warning(f"[real-ingest] parallel known-handle query failed: {e}")
    finally:
        await client.close()

    # Merge by id — same tweet may appear in both queries
    raw_merged = raw_main
    if raw_parallel:
        seen_ids = {r.id for r in raw_main}
        raw_merged = list(raw_main) + [r for r in raw_parallel if r.id not in seen_ids]

    pipe = Pipeline()
    out = pipe.run(raw_merged)

    # Persist surfaced + demoted so the feed / review queue / retraining
    # loop see every tweet that made it through the pipeline. Issue 5:
    # known-handle tweets bypass the per-beat cap when priority_bypass=True.
    database = db()
    to_persist = list(out.surfaced) + list(getattr(out, "demoted", []))
    # Layer B Addition 1 + 4: classify tweet types, cluster tweets into
    # topics, and stamp topic_id + tweet_type onto each tweet. Runs BEFORE
    # the upsert loop so the DB rows include the new columns.
    from ..services.topic_grouper_wrapper import cluster_and_persist, maybe_fire_reactive_expansion
    try:
        clusters = cluster_and_persist(to_persist, database)
    except Exception as e:
        logger.warning(f"[real-ingest] clustering skipped: {e}")
        clusters = []
    # Layer B Addition 5 — fire-and-forget reactive expansion for any
    # cluster of size >= 2. The function schedules an asyncio task and
    # returns immediately; the task runs in the background.
    try:
        maybe_fire_reactive_expansion(clusters)
    except Exception as e:
        logger.warning(f"[real-ingest] reactive expansion schedule skipped: {e}")
    persisted = 0
    is_mock = False  # real-ingest: never mock
    for st in to_persist:
        is_known = priority_bypass and is_known_any(st.raw.author_handle)
        if not is_known and persisted >= persist_budget:
            # Budget exhausted; skip non-known tweets
            continue
        database.upsert_tweet({
            "id": st.raw.id,
            "author_id": st.raw.author_id,
            "author_handle": st.raw.author_handle,
            "text": st.raw.text,
            "clean_text": st.clean.clean_text,
            "lang": st.raw.lang,
            "created_at": st.raw.created_at,
            "processed_at": st.processed_at,
            "bot_score": st.clean.bot_score,
            "bot_label": st.clean.bot_label.value,
            "relevance_score": st.clean.relevance_score,
            "quality_score": st.clean.quality_score,
            "credibility_score": st.credibility_score,
            "credibility_level": st.credibility_level.value,
            "final_score": st.final_score,
            "passed_all_stages": st.passed_all_stages,
            "software_focus_passed": st.clean.software_focus_passed,
            "software_focus_meta": list(st.clean.software_focus_meta or []),
            "embedding": st.embedding,
            "payload": {
                "bot_reasons": st.clean.bot_reasons,
                "credibility_reasons": st.credibility_reasons,
                "noise_score": st.clean.noise_score,
                "noise_labels": st.clean.noise_labels,
            },
        })
        persisted += 1
    if out.review_queue:
        review_queue().push(out.review_queue)

    return persisted


# ---------------------------------------------------------------------
# Mock ingest (for development without API credits)
# ---------------------------------------------------------------------

@router.post("/ingest/mock", response_model=IngestResponse)
async def ingest_mock(
    n: int = Query(20, ge=1, le=200),
    seed: int = Query(42),
) -> IngestResponse:
    """Generate synthetic tweets and run them through the pipeline.

    Useful for demos, CI, and local development when the real API has no
    credits. Mirrors the response shape of /api/ingest.
    """
    return _run_mock_ingest(n=n, seed=seed)


def _run_mock_ingest(n: int = 20, seed: Optional[int] = 42) -> IngestResponse:
    """Core mock-ingest pipeline (synchronous).

    Generates `n` synthetic tweets, runs them through the full 6-stage
    pipeline, upserts surfaced items to the DB, and pushes the active-learning
    review queue. Returns the same IngestResponse shape as the HTTP endpoint.

    Shared by:
      - `POST /api/ingest/mock` (HTTP entry point)
      - the `_mock_autoseed_task` background task in `app/main.py` (kiosk mode)

    Pass `seed=None` for non-deterministic seeding (used by autoseed so each
    tick generates fresh data instead of repeating the same tweets).
    """
    import random
    from datetime import datetime, timezone, timedelta

    from ..models.schemas import RawTweet

    rng = random.Random(seed if seed is not None else random.randint(0, 2**31 - 1))
    # ------------------------------------------------------------------
    # Mock data — software / AI / ML sphere.
    # Every human handle carries a software-sphere bio so the Stage 0
    # `SoftwareFocusFilter` (in `stage_software_focus.py`) lets them through
    # via the `bio_keyword_match` path. Bot handles stay empty-biosed and
    # use crypto/airdrop language so they're visibly rejected at the
    # `tweet_scam_terms` sub-check — useful for the demo.
    # ------------------------------------------------------------------
    # (handle, followers, verified, account_age_days, description)
    handles_human = [
        # Fictional verified orgs
        ("fable_ai", 180000, True, 900, "ai research lab — frontier model research"),
        ("mineral_lab", 220000, True, 1200, "ml research org, open source releases"),
        ("ledger_models", 95000, True, 700, "open source foundation for transformer tooling"),
        ("emberstack", 60000, True, 500, "developer tools and api platform"),
        ("polycli", 45000, True, 420, "polyglot programming language community"),
        ("northwave_dl", 130000, True, 1100, "deep learning engineering team, pytorch + cuda"),
        # Fictional unverified practitioners
        ("ada_codes", 3200, False, 900, "ml engineer • python • pytorch"),
        ("kestrel_dev", 5400, False, 1400, "rust + golang backend engineer, microservices"),
        ("mira_open", 2100, False, 800, "open source maintainer, contributing to react"),
        ("soren_mlops", 4800, False, 1100, "mlops + kubernetes + terraform, sre"),
        ("jie_l", 7200, False, 1600, "deep learning researcher, transformers, nlp"),
    ]
    # (handle, followers, account_age_days, description)
    handles_bot = [
        ("promo_king", 50, 30, ""),
        ("deal_hunter_24", 30, 15, ""),
        ("click4cash", 80, 10, ""),
        ("free_iphone_now", 20, 5, ""),
        ("crypto_signals_x", 60, 20, ""),
        ("make_money_fast", 40, 10, ""),
    ]
    templates_human = [
        "Anthropic released a new version of Claude — claiming better benchmark scores on coding and reasoning tasks. Paper linked in the release notes.",
        "We benchmarked llama vs claude vs gpt on our internal eval suite — results and methodology in the paper. Inference latency was the surprise.",
        "Next.js 16 just shipped with improved build performance. Opened a PR upstream to add migration notes from v15.",
        "Kubernetes 1.32 release notes are out — the new sidecar feature changes how we run our service mesh. Breaking change for our deployment.",
        "NeurIPS 2026 papers list is live — multiple papers on transformer attention and inference optimization this year. Going to be a packed schedule.",
        "GitHub just made the API rate limit change and broke our build. Here's the patch I opened upstream and the migration in our repo.",
        "We migrated our inference pipeline from pytorch to vllm — latency dropped 3x, here's the new architecture and the benchmark numbers.",
        "Stripe shared a great engineering blog post about their API design choices and why they deprecated that endpoint. Worth a read.",
        "New release of pytorch with improved compilation — eager mode is finally competitive with the compiled path for our training workload.",
        "Hot take: most \"AI agent\" demos I see are just a wrapper around an api call to gpt and a brittle prompt. Show me the architecture, not the demo video.",
        "Just merged the migration to postgres 17 in our repo. The performance improvement on our analytics queries is real.",
        "TypeScript 5.7 release notes: the new type system improvements clean up a lot of legacy code in our api handlers.",
        "Docker build cache invalidation after a dependency update is the source of 80% of our CI pain. Filed an issue upstream.",
        "Our team adopted a code review checklist after the last incident — turned out to be the highest-leverage change we made this quarter.",
        # Near-duplicate templates — used 2-3 times per batch so the topic
        # clustering has real corroboration to work with. The Issue 4 dedup
        # fix (Stage 2) would otherwise drop these as duplicates, but the
        # MinHash keep-both-for-known-handles path is what makes this OK.
        "Anthropic released a new version of Claude with breakthrough benchmark scores today on coding and reasoning tasks.",
        "Anthropic just released a brand new Claude model with breakthrough benchmark scores on coding tasks today.",
    ]
    templates_bot = [
        "BUY NOW click here for free bitcoin 🚀🚀🚀🚀🚀 http://spam.example",
        "make $5000/day with this one weird trick click here http://spam.example",
        "dm me for crypto signals guaranteed returns 🚀🚀🚀🚀 #crypto #bitcoin",
        "looking for promo! link in bio onlyfans premium content http://spam.example",
        "FREE followers in 24 hours click here http://spam.example",
        "cheap shoes http://spam.example #shoes #sale #fashion",
        "click here for free iphone! http://spam.example",
        "hot singles in your area want to chat dm me",
    ]
    topics = [
        "transformer", "inference", "benchmark", "rust", "python",
        "open source", "mlops", "kubernetes", "react", "release",
    ]
    hashtags_human = [
        "#python", "#rustlang", "#kubernetes", "#opensource",
        "#mlops", "#devops", "#webdev", "#ai", "#claude",
    ]
    hashtags_bot = ["#deal", "#win", "#free", "#sale", "#limited", "#crypto", "#pump", "#bitcoin"]

    now = datetime.now(timezone.utc)
    raws: list[RawTweet] = []
    for i in range(n):
        if rng.random() < 0.65:
            h, foll, ver, age_days, desc = rng.choice(handles_human)
            text = rng.choice(templates_human)
            # ~50% of human tweets pick up 1-2 topical hashtags
            if rng.random() < 0.5:
                tags = rng.sample(hashtags_human, rng.randint(1, 2))
                text = text + " " + " ".join(tags)
        else:
            h, foll, age_days, desc = rng.choice(handles_bot)
            text = rng.choice(templates_bot)
            ver = False

        raws.append(RawTweet(
            id=str(rng.randint(10**15, 10**16)),
            text=text,
            author_id=str(rng.randint(10**6, 10**9)),
            author_handle=h,
            author_display_name=h,
            author_followers=foll,
            author_following=min(foll, rng.randint(50, 2000)),
            author_verified=ver,
            author_created_at=now - timedelta(days=age_days),
            author_profile_image_url=None if not ver else f"https://x.com/{h}.jpg",
            author_description=desc,
            lang="en",
            created_at=now - timedelta(minutes=rng.randint(0, 240)),
            hashtags=[w for w in text.split() if w.startswith("#")],
            urls=[w for w in text.split() if w.startswith("http")],
            mentions=[w for w in text.split() if w.startswith("@")],
            media=[],
            # Unverified humans get enough engagement to clear min_engagement=5
            # in `SoftwareFocusFilter._too_low_engagement`. Bots stay at 0.
            like_count=rng.randint(20, 500) if ver else rng.randint(5, 50),
            retweet_count=rng.randint(5, 100) if ver else rng.randint(1, 10),
            reply_count=rng.randint(2, 30) if ver else rng.randint(0, 5),
            quote_count=rng.randint(0, 20) if ver else rng.randint(0, 3),
        ))

    pipe = Pipeline()
    out = pipe.run(raws)

    # Persist BOTH surfaced (above the credibility floor) and demoted items
    # (passed all stages but below the floor). This mirrors what the real
    # /api/ingest path does, so the feed / review queue / retraining loop
    # see every tweet that made it through the pipeline. Items below the
    # floor stay in the DB but don't surface to the dashboard until the
    # surface_min_credibility cutoff is met.
    database = db()
    to_persist = list(out.surfaced) + list(getattr(out, "demoted", []))
    # Layer B Addition 1 + 4: cluster + classify type. Stub (no
    # reactive expansion in mock mode — that needs the live API).
    from ..services.topic_grouper_wrapper import cluster_and_persist
    try:
        cluster_and_persist(to_persist, database, is_mock=True)
    except Exception as e:
        logger.warning(f"[mock] clustering skipped: {e}")
    for st in to_persist:
        database.upsert_tweet({
            "id": st.raw.id,
            "author_id": st.raw.author_id,
            "author_handle": st.raw.author_handle,
            "text": st.raw.text,
            "clean_text": st.clean.clean_text,
            "lang": st.raw.lang,
            "created_at": st.raw.created_at,
            "processed_at": st.processed_at,
            "bot_score": st.clean.bot_score,
            "bot_label": st.clean.bot_label.value,
            "relevance_score": st.clean.relevance_score,
            "quality_score": st.clean.quality_score,
            "credibility_score": st.credibility_score,
            "credibility_level": st.credibility_level.value,
            "final_score": st.final_score,
            "passed_all_stages": st.passed_all_stages,
            "software_focus_passed": st.clean.software_focus_passed,
            "software_focus_meta": list(st.clean.software_focus_meta or []),
            "embedding": st.embedding,
            "payload": {
                "bot_reasons": st.clean.bot_reasons,
                "credibility_reasons": st.credibility_reasons,
                "noise_score": st.clean.noise_score,
                "noise_labels": st.clean.noise_labels,
            },
        })
    if out.review_queue:
        review_queue().push(out.review_queue)

    return IngestResponse(
        job_id=datetime.utcnow().isoformat(),
        query="mock",
        fetched=len(raws),
        surfaced=len(out.surfaced),
        review_queue=len(out.review_queue),
        stats=out.stats,
    )


async def _poll_loop(query: str, every: int, max_results: int) -> None:
    client = TwitterClient()
    try:
        while True:
            try:
                raw = await client.search_tweets(query, max_results=max_results)
                if raw:
                    pipe = Pipeline()
                    out = pipe.run(raw)
                    database = get_database()
                    for st in out.surfaced:
                        database.upsert_tweet({
                            "id": st.raw.id,
                            "author_id": st.raw.author_id,
                            "author_handle": st.raw.author_handle,
                            "text": st.raw.text,
                            "clean_text": st.clean.clean_text,
                            "lang": st.raw.lang,
                            "created_at": st.raw.created_at,
                            "processed_at": st.processed_at,
                            "bot_score": st.clean.bot_score,
                            "bot_label": st.clean.bot_label.value,
                            "relevance_score": st.clean.relevance_score,
                            "quality_score": st.clean.quality_score,
                            "credibility_score": st.credibility_score,
                            "credibility_level": st.credibility_level.value,
                            "final_score": st.final_score,
                            "passed_all_stages": st.passed_all_stages,
                            "embedding": st.embedding,
                            "payload": {
                                "bot_reasons": st.clean.bot_reasons,
                                "credibility_reasons": st.credibility_reasons,
                            },
                        })
                    if out.review_queue:
                        ReviewQueue().push(out.review_queue)
            except Exception as e:
                logger.warning(f"poll loop error: {e}")
            await asyncio.sleep(every)
    finally:
        await client.close()


# ---------------------------------------------------------------------
# Review queue endpoints
# ---------------------------------------------------------------------

@router.get("/review/queue")
async def review_queue_endpoint(
    limit: int = Query(25, ge=1, le=200),
) -> dict:
    items = review_queue().next_batch(limit)
    return {"items": items, "stats": review_queue().stats()}


@router.post("/review/{review_id}/label")
async def label_review(review_id: str, req: LabelRequest) -> dict:
    ok = review_queue().label(
        review_id, req.label.value, req.category, req.notes, req.labeler_id
    )
    if not ok:
        raise HTTPException(404, "review not found")
    return {"status": "ok", "review_id": review_id, "label": req.label.value}


@router.get("/review/stats")
async def review_stats() -> dict:
    return review_queue().stats()


# ---------------------------------------------------------------------
# Pipeline stats
# ---------------------------------------------------------------------

@router.get("/stats", response_model=PipelineStats)
async def stats() -> PipelineStats:
    """Aggregate counts from the most recent run."""
    with db().session() as s:
        from sqlalchemy import func, select
        from ..models.db_models import TweetORM, ReviewORM

        ingested = s.execute(select(func.count(TweetORM.id))).scalar() or 0
        surfaced = (
            s.execute(
                select(func.count(TweetORM.id)).where(TweetORM.passed_all_stages.is_(True))
            ).scalar()
            or 0
        )
        ai_pass = (
            s.execute(
                select(func.count(TweetORM.id)).where(TweetORM.software_focus_passed.is_(True))
            ).scalar()
            or 0
        )
        in_queue = (
            s.execute(select(func.count(ReviewORM.id)).where(ReviewORM.label.is_(None))).scalar()
            or 0
        )
    return PipelineStats(
        ingested=ingested,
        passed_software_focus=ai_pass,
        rejected_software_focus=ingested - ai_pass,
        surfaced=surfaced,
        in_review_queue=in_queue,
        passed_api_filter=surfaced,        # approximation, real stats logged per-run
        passed_cleaning=surfaced,
        passed_bot_filter=surfaced,
        passed_relevance=surfaced,
        passed_credibility=surfaced,
        last_run_at=datetime.utcnow(),
    )


# ---------------------------------------------------------------------
# Feedback (like / dislike)
# ---------------------------------------------------------------------

@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest) -> dict:
    """Record a 👍/👎 signal on a tweet. Drives active learning."""
    from datetime import datetime, timezone
    from uuid import uuid4

    database = db()
    # snapshot the tweet for retraining context
    snap = database.get_one(req.tweet_id)
    database.record_feedback(
        feedback_id=uuid4().hex,
        tweet_id=req.tweet_id,
        signal=req.signal.value,
        category=req.category,
        notes=req.notes,
        user_id=req.user_id,
        snapshot=snap,
    )
    logger.info(
        f"feedback: tweet={req.tweet_id[:12]}… signal={req.signal.value} "
        f"category={req.category or '-'} user={req.user_id}"
    )
    return {"status": "ok", "tweet_id": req.tweet_id, "signal": req.signal.value}


@router.get("/feedback/aggregates")
async def feedback_aggregates(tweet_ids: Optional[str] = None) -> dict:
    """Return {tweet_id: {up, down}} for the given comma-separated ids."""
    ids = [t for t in (tweet_ids or "").split(",") if t]
    database = db()
    return database.feedback_aggregates(ids if ids else None)


@router.get("/feedback/summary")
async def feedback_summary() -> dict:
    """Aggregate stats for the active-learning dashboard."""
    database = db()
    return database.feedback_summary()


@router.get("/feedback/for/{tweet_id}")
async def feedback_for_tweet(tweet_id: str) -> dict:
    """Return all feedback signals for a tweet."""
    database = db()
    return {"tweet_id": tweet_id, "signals": database.feedback_for_tweet(tweet_id)}


# ---------------------------------------------------------------------
# ML health (for retraining)
# ---------------------------------------------------------------------

@router.get("/ml/metrics")
async def ml_metrics() -> dict:
    """Recent model metrics for the continuous-improvement dashboard."""
    database = db()
    bot = database.recent_metrics("bot_classifier")
    cred = database.recent_metrics("credibility")
    return {
        "bot_classifier": [
            {"metric": m["metric_name"], "value": m["metric_value"],
             "recorded_at": m["recorded_at"],
             "version": m["version"], "sample_size": m["sample_size"]}
            for m in bot
        ],
        "credibility": [
            {"metric": m["metric_name"], "value": m["metric_value"],
             "recorded_at": m["recorded_at"],
             "version": m["version"], "sample_size": m["sample_size"]}
            for m in cred
        ],
    }


@router.post("/ml/retrain")
async def trigger_retrain(background: BackgroundTasks) -> dict:
    """Kick off a retrain task (Celery in prod, in-process here for MVP)."""
    from ..ml.retrain import retrain_bot_classifier

    background.add_task(retrain_bot_classifier.delay if hasattr(retrain_bot_classifier, "delay") else retrain_bot_classifier)
    return {"status": "queued"}


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _row_to_scored(row: dict) -> ScoredTweet:
    """Build a ScoredTweet from a serialized DB row."""
    from ..models.schemas import (
        BotPrediction,
        CleanedTweet,
        RawTweet,
        ScoredTweet,
    )

    payload = row.get("payload") or {}
    raw = RawTweet(
        id=row["id"],
        text=row.get("text", ""),
        author_id=row.get("author_id", ""),
        author_handle=row.get("author_handle", ""),
        author_display_name=row.get("author_display_name", ""),
        author_followers=row.get("author_followers", 0),
        author_verified=row.get("author_verified", False),
        created_at=row.get("created_at") or datetime.utcnow(),
    )
    clean = CleanedTweet(
        raw=raw,
        clean_text=row.get("clean_text", ""),
        tokens=[],
        lemmas=[],
        minhash_signature=None,
        language=row.get("lang") or "und",
    )
    clean.bot_score = float(row.get("bot_score", 0.0))
    clean.bot_label = BotPrediction(row.get("bot_label", "uncertain"))
    clean.relevance_score = float(row.get("relevance_score", 0.0))
    clean.quality_score = float(row.get("quality_score", 0.0))
    clean.embedding = row.get("embedding")
    bot_reasons = payload.get("bot_reasons", [])
    cred_reasons = payload.get("credibility_reasons", [])
    return ScoredTweet(
        raw=raw,
        clean=clean,
        embedding=row.get("embedding"),
        bot_score=clean.bot_score,
        bot_label=clean.bot_label,
        bot_reasons=bot_reasons,
        relevance_score=clean.relevance_score,
        quality_score=clean.quality_score,
        credibility_score=float(row.get("credibility_score", 0.0)),
        credibility_level=CredibilityLevel(row.get("credibility_level", "unverified")),
        credibility_reasons=cred_reasons,
        final_score=float(row.get("final_score", 0.0)),
        passed_all_stages=bool(row.get("passed_all_stages", False)),
        processed_at=row.get("processed_at") or datetime.utcnow(),
    )