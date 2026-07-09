"""Pipeline correctness fixes — silent-failure bugs caught by Sub-agent B.

Each test corresponds to a bug caught in `backend/app/pipeline/` or
`backend/app/services/known_handles.py` during the autonomous improvement
loop. The tests are intentionally tight: they assert the *good* behavior
after the fix, so they would fail against the pre-fix code.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models.schemas import (
    BotPrediction,
    CleanedTweet,
    CredibilityLevel,
    RawTweet,
    ScoredTweet,
)
from app.pipeline import (
    ApiFilter,
    CredibilityScorer,
    Pipeline,
    RelevanceFilter,
    TextCleaner,
)
from app.pipeline.stage2_text_clean import TextCleaner as _TextCleaner


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _mk(
    text: str = "Some valid tweet text that should be processed normally.",
    *,
    handle: str = "user",
    followers: int = 1000,
    is_bot: bool = False,
    verified: bool = False,
    lang: str = "en",
    age_days: int = 1500,
) -> RawTweet:
    now = datetime.now(timezone.utc)
    return RawTweet(
        id=str(abs(hash(text + handle)) % (10**15)),
        text=text,
        author_id="1",
        author_handle=handle,
        author_display_name=handle,
        author_followers=followers,
        author_following=followers // 2,
        author_verified=verified or not is_bot,
        author_created_at=now - timedelta(days=30 if is_bot else age_days),
        author_profile_image_url=None if is_bot else "https://x.com/x.jpg",
        author_description="" if is_bot else "regular user",
        lang=lang,
        created_at=now,
        hashtags=[w for w in text.split() if w.startswith("#")],
        urls=[w for w in text.split() if w.startswith("http")],
        mentions=[w for w in text.split() if w.startswith("@")],
        media=[],
        like_count=1 if is_bot else 50,
        retweet_count=0,
        reply_count=0,
        quote_count=0,
    )


# ---------------------------------------------------------------------
# Bug A — Stage 2 silently drops tweets when _clean raises.
# The `try / except Exception` block around _clean used to `continue`,
# which made the tweet vanish from `result.passed + result.rejected` while
# `result.stats["input"]` still counted it. Downstream consumers saw a
# mismatch and couldn't tell which tweet was lost.
# Fix: catch the exception, log the full traceback, and append the tweet
# to `rejected` with a `processing_failed:<exc-class>` reason so the caller
# can audit / retry.
# ---------------------------------------------------------------------

def test_stage2_does_not_silently_drop_tweet_on_clean_failure(monkeypatch):
    """When _clean raises for a specific tweet, the tweet must show up in
    `rejected` with a `processing_failed` reason — not vanish from stats."""
    cleaner = _TextCleaner(compute_minhash=False)

    def boom(tw):
        raise ValueError("simulated processing failure")

    monkeypatch.setattr(cleaner, "_clean", boom)

    raw = _mk()
    result = cleaner.process([raw])

    # The tweet must appear in one of passed/rejected — never vanish.
    assert result.stats["input"] == 1
    assert result.stats["passed"] + result.stats["rejected"] == 1, (
        f"tweet silently dropped: stats={result.stats} "
        f"passed={len(result.passed)} rejected={len(result.rejected)}"
    )
    # And the rejection reason must identify it as a processing failure so
    # downstream tooling can audit / retry.
    assert len(result.rejected) == 1
    reason = result.rejected[0][1]
    assert reason.startswith("processing_failed"), f"unexpected reason: {reason}"
    assert "ValueError" in reason


def test_stage2_does_not_silently_drop_when_one_of_many_fails(monkeypatch):
    """A single failing tweet among many should not abort the batch — the
    others should still pass through."""
    cleaner = _TextCleaner(compute_minhash=False)

    # _clean raises only for one specific tweet id.
    def maybe_boom(tw):
        if tw.id == "bad":
            raise RuntimeError("intentional")
        # delegate to the real impl
        return _TextCleaner._clean(cleaner, tw)

    monkeypatch.setattr(cleaner, "_clean", maybe_boom)

    bad = RawTweet(
        id="bad", text="bad tweet text", author_id="1", author_handle="user",
        author_display_name="u", author_followers=1000, lang="en",
        created_at=datetime.now(timezone.utc),
    )
    good = _mk()
    result = cleaner.process([bad, good])

    assert result.stats["input"] == 2
    assert result.stats["passed"] + result.stats["rejected"] == 2
    assert result.stats["rejected"] == 1
    assert result.stats["passed"] == 1


# ---------------------------------------------------------------------
# Bug B — `is_mock` flag is not propagated for fresh mock tweets.
# `cluster_and_persist(..., is_mock=True)` runs BEFORE `upsert_tweet()`,
# so when it does `s.get(TweetORM, st.raw.id)` it returns None and the
# is_mock assignment is a no-op. Then `upsert_tweet()` creates the ORM
# without setting is_mock, so it defaults to False and the mock tweet
# leaks into the live dashboard.
# Fix: `upsert_tweet` reads `is_mock` from the input dict so callers can
# stamp it on freshly-created rows.
# ---------------------------------------------------------------------

def test_upsert_tweet_stamps_is_mock_for_fresh_rows(tmp_path):
    """A freshly-created tweet should pick up is_mock from the upsert dict
    so mock data stays out of the live dashboard."""
    from app.services.db import Database

    db = Database(f"sqlite:///{tmp_path}/test_is_mock.db")
    db.init()

    raw = RawTweet(
        id="mock_1",
        text="mock tweet text",
        author_id="a1",
        author_handle="alice",
        author_display_name="Alice",
        author_followers=1000,
        lang="en",
        created_at=datetime.now(timezone.utc),
    )
    # Fresh row — never existed before. cluster_and_persist would have
    # been a no-op for is_mock here.
    db.upsert_tweet({
        "id": raw.id,
        "author_id": raw.author_id,
        "author_handle": raw.author_handle,
        "text": raw.text,
        "clean_text": raw.text,
        "lang": raw.lang,
        "created_at": raw.created_at,
        "processed_at": datetime.now(timezone.utc),
        "bot_score": 0.0,
        "bot_label": "human",
        "relevance_score": 0.8,
        "quality_score": 0.6,
        "credibility_score": 0.8,
        "credibility_level": "high",
        "final_score": 0.7,
        "passed_all_stages": True,
        "software_focus_passed": True,
        "software_focus_meta": [],
        "embedding": None,
        "payload": {},
        "is_mock": True,
    })
    row = db.get_one(raw.id)
    assert row is not None
    assert row["is_mock"] is True, (
        "freshly-upserted tweet did not pick up is_mock=True from the "
        "upsert dict — mock data would leak into the live dashboard"
    )


def test_upsert_tweet_is_mock_defaults_false(tmp_path):
    """When the upsert dict doesn't include is_mock, the row should default
    to False (real-ingest semantics)."""
    from app.services.db import Database

    db = Database(f"sqlite:///{tmp_path}/test_is_mock2.db")
    db.init()

    raw = RawTweet(
        id="real_1",
        text="real tweet text",
        author_id="a1",
        author_handle="reuters",
        author_display_name="Reuters",
        author_followers=200_000,
        lang="en",
        created_at=datetime.now(timezone.utc),
    )
    db.upsert_tweet({
        "id": raw.id,
        "author_id": raw.author_id,
        "author_handle": raw.author_handle,
        "text": raw.text,
        "clean_text": raw.text,
        "lang": raw.lang,
        "created_at": raw.created_at,
        "processed_at": datetime.now(timezone.utc),
        "bot_score": 0.0,
        "bot_label": "human",
        "relevance_score": 0.8,
        "quality_score": 0.6,
        "credibility_score": 0.8,
        "credibility_level": "high",
        "final_score": 0.7,
        "passed_all_stages": True,
        "software_focus_passed": True,
        "software_focus_meta": [],
        "embedding": None,
        "payload": {},
    })
    row = db.get_one(raw.id)
    assert row is not None
    assert row["is_mock"] is False


# ---------------------------------------------------------------------
# Bug C — `_find_dup` returns None on exception (silent fail-open).
# If `mh_obj.jaccard(past_mh)` raises (e.g., datasketch scheme mismatch
# after a future upgrade), the tweet is treated as NOT a duplicate and
# let through. This silently bypasses near-duplicate detection — two
# near-identical tweets would both surface to the feed.
# Fix: log the exception with the full traceback and treat it as a
# non-duplicate (fail-open), so a transient MinHash error doesn't drop
# good tweets. The error must be visible.
# ---------------------------------------------------------------------

def test_find_dup_logs_on_jaccard_failure():
    """When jaccard() raises inside _find_dup, the exception must be
    caught AND logged so operators can see it — not silently swallowed
    and not propagated up to the caller."""
    from loguru import logger
    from app.pipeline.stage2_text_clean import TextCleaner

    cleaner = TextCleaner(compute_minhash=True, num_perm=16)

    # Build a fake "past" MinHash by constructing a real one.
    from datasketch import MinHash
    fake_past = MinHash(num_perm=16, scheme="affine32")
    fake_past.update(b"the quick brown fox")
    cleaner._minhash_seen.append((fake_past, _mk_cleaned_stub()))

    # Force jaccard to raise.
    fake_new = MinHash(num_perm=16, scheme="affine32")
    fake_new.update(b"the quick brown fox")

    class BrokenMinHash:
        hashvalues = fake_new.hashvalues
        def jaccard(self, other):
            raise RuntimeError("simulated jaccard failure")

    # Capture loguru output by adding a temporary sink.
    captured = []

    def sink(message):
        captured.append(str(message))

    handler_id = logger.add(sink, level="ERROR")
    try:
        # Must not raise — the fix catches and logs the exception.
        result = cleaner._find_dup(BrokenMinHash())
        assert result is None, (
            "expected _find_dup to fail-open to None when jaccard() raises"
        )
    finally:
        logger.remove(handler_id)

    # And the failure must be visible to operators via the traceback log.
    assert any(
        "jaccard" in msg.lower() and "simulated jaccard failure" in msg.lower()
        for msg in captured
    ), f"expected _find_dup to log the failure; got: {captured}"
    assert any(
        "find_dup" in msg.lower() for msg in captured
    ), f"expected log message to mention find_dup; got: {captured}"


def _mk_cleaned_stub() -> CleanedTweet:
    raw = _mk()
    return CleanedTweet(
        raw=raw,
        clean_text=raw.text.lower(),
        tokens=raw.text.lower().split(),
        lemmas=raw.text.lower().split(),
        language="en",
    )


# ---------------------------------------------------------------------
# Bug D — Stage 5 must propagate the embedding from CleanedTweet to
# ScoredTweet so topic_grouper can cluster them.
# Earlier commits fixed a bug where `ScoredTweet.embedding` was always
# None. Verify the fix didn't regress by mocking the embedder and
# asserting that `scored.embedding` is the vector Stage 4 produced.
# ---------------------------------------------------------------------

def test_stage5_propagates_embedding_from_stage4(monkeypatch):
    """Stage 5 must copy Stage 4's embedding onto the ScoredTweet, so
    clustering sees the vector. Earlier commits fixed this — guard
    against regression."""
    import numpy as np
    import app.pipeline.stage4_relevance as s4mod
    from app.pipeline.stage5_credibility import CredibilityScorer

    # Mock the embedder to return a deterministic vector. _Embedder.get()
    # returns either a model or `False`; we need something that has an
    # `.encode(texts, **kwargs)` method that returns a numpy array.
    expected_vec = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)

    class FakeModel:
        def encode(self, texts, **kwargs):
            return np.tile(expected_vec, (len(texts), 1))

    s4mod._Embedder._model = None  # force lazy-load path
    monkeypatch.setattr(s4mod._Embedder, "get", classmethod(lambda cls, model_name=None: FakeModel()))

    raw = _mk(text="Reuters reports central bank rate decision today",
              handle="reuters", verified=True, followers=200_000)

    # Run through stages 1 -> 4.
    s1 = ApiFilter(min_followers=200).process([raw]).passed
    s2 = TextCleaner(compute_minhash=False).process(s1).passed
    s4 = RelevanceFilter(relevance_threshold=0.0).process(s2).passed
    assert s4[0].embedding is not None, "Stage 4 should set embedding"
    assert s4[0].embedding == list(expected_vec), (
        f"Stage 4 produced wrong embedding: {s4[0].embedding}"
    )

    # Run Stage 5 and check embedding propagates.
    s5 = CredibilityScorer().process(s4).passed
    assert s5[0].embedding is not None, "Stage 5 must propagate embedding"
    assert s5[0].embedding == list(expected_vec), (
        f"Stage 5 lost Stage 4's embedding: got {s5[0].embedding}"
    )