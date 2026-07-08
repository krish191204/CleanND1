"""WebSocket for real-time feed updates (Phase 2)."""
from __future__ import annotations

import asyncio
import json
from typing import Set

from fastapi import WebSocket, WebSocketDisconnect

from ..services import get_database
from ..services.cards import to_card


class ConnectionManager:
    def __init__(self) -> None:
        self.active: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)

    async def broadcast(self, message: dict) -> None:
        if not self.active:
            return
        text = json.dumps(message, default=str)
        dead: list[WebSocket] = []
        for ws in list(self.active):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


async def feed_push_loop() -> None:
    """Every N seconds, broadcast the latest 10 surfaced cards."""
    last_seen_ids: set[str] = set()
    while True:
        try:
            database = get_database()
            orms = database.get_surfaced(limit=10)
            new_cards = []
            for orm in orms:
                if orm.id in last_seen_ids:
                    continue
                from .routes import _to_scored

                new_cards.append(to_card(_to_scored(orm)).model_dump(mode="json"))
                last_seen_ids.add(orm.id)
            if new_cards:
                await manager.broadcast({"type": "feed_update", "items": new_cards})
            # cap the dedup set
            if len(last_seen_ids) > 500:
                last_seen_ids = set(list(last_seen_ids)[-250:])
        except Exception:
            pass
        await asyncio.sleep(10)