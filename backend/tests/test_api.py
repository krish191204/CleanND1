"""API smoke tests using FastAPI's TestClient."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("data") / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["BOT_MODEL_PATH"] = str(tmp_path_factory.mktemp("ml") / "bot.joblib")
    # Disable the mock auto-seed background task so it doesn't run during
    # tests and pollute the DB / interfere with count assertions.
    os.environ["MOCK_AUTO_SEED_ENABLED"] = "false"
    # also point at a fresh cache so the cached settings singleton doesn't leak
    from app.config import get_settings
    get_settings.cache_clear()

    from app.main import app
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ingest_mock_and_feed(client):
    r = client.post("/api/ingest/mock?n=20&seed=42")
    assert r.status_code == 200
    body = r.json()
    assert body["fetched"] == 20
    assert body["stats"]["ingested"] == 20
    assert body["surfaced"] >= 0

    feed = client.get("/api/feed?limit=10")
    assert feed.status_code == 200
    items = feed.json()["items"]
    assert isinstance(items, list)
    if items:
        first = items[0]
        assert "headline" in first
        assert "credibility_level" in first
        assert "why_shown" in first


def test_review_label_flow(client):
    # push some items
    client.post("/api/ingest/mock?n=30&seed=99")
    queue = client.get("/api/review/queue?limit=5")
    assert queue.status_code == 200
    items = queue.json()["items"]
    if items:
        rid = items[0]["id"]
        r = client.post(
            f"/api/review/{rid}/label",
            json={"label": "approved", "category": "tech", "notes": "looks legit"},
        )
        assert r.status_code == 200
        stats = client.get("/api/review/stats").json()
        assert stats["labeled"] >= 1


def test_ml_metrics_endpoint(client):
    r = client.get("/api/ml/metrics")
    assert r.status_code == 200
    body = r.json()
    assert "bot_classifier" in body
    assert "credibility" in body
