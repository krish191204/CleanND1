"""Tests for the tweet-type classifier (Layer B Addition 4)."""
from __future__ import annotations

import os
import json
import tempfile
from datetime import datetime, timezone

import pytest

from app.models.schemas import (
    BotPrediction,
    CleanedTweet,
    CredibilityLevel,
    RawTweet,
    ScoredTweet,
    TweetType,
)
from app.pipeline.tweet_type import classify_tweet_type


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _mk(handle: str, text: str) -> ScoredTweet:
    raw = RawTweet(
        id=f"t_{hash(text + handle) & 0xFFFFFFFF:x}",
        text=text, author_id="1", author_handle=handle,
        author_display_name=handle, author_followers=1000,
        author_following=500, author_verified=True,
        author_created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        author_profile_image_url=None, author_description="x",
        lang="en", created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        hashtags=[], urls=[], mentions=[], media=[],
        like_count=10, retweet_count=0, reply_count=0, quote_count=0,
    )
    clean = CleanedTweet(
        raw=raw, clean_text=text.lower(),
        tokens=text.lower().split(), lemmas=text.lower().split(),
        minhash_signature=None, language="en",
    )
    return ScoredTweet(
        raw=raw, clean=clean,
        bot_score=0.0, bot_label=BotPrediction.HUMAN,
        relevance_score=0.8, quality_score=0.6,
        credibility_score=0.8, credibility_level=CredibilityLevel.HIGH,
        final_score=0.7, passed_all_stages=True,
    )


@pytest.fixture
def known_handles_env(tmp_path, monkeypatch):
    """Set up tiny known-handles JSONs so the singleton resolves our test
    handles correctly. monkeypatch reverts the env vars after the test."""
    news_p = tmp_path / "news.json"
    news_p.write_text(json.dumps({
        "ai_orgs": ["openai", "anthropicai", "reuters"],
    }))
    ind_p = tmp_path / "ind.json"
    ind_p.write_text(json.dumps({
        "ai_researchers": ["karpathy", "ylecun", "simonw"],
    }))
    monkeypatch.setenv("CREDIBILITY_KNOWN_NEWS_HANDLES_PATH", str(news_p))
    monkeypatch.setenv("KNOWN_CREDIBLE_INDIVIDUALS_PATH", str(ind_p))
    from app.services import known_handles
    known_handles.reset_cache()
    yield
    known_handles.reset_cache()


def test_announcement_for_known_news_handle(known_handles_env):
    t = _mk("openai", "We're releasing a brand new GPT model with breakthrough capabilities today.")
    out = classify_tweet_type(t)
    assert out == TweetType.ANNOUNCEMENT
    assert t.tweet_type == TweetType.ANNOUNCEMENT


def test_opinion_for_known_individual(known_handles_env):
    t = _mk("karpathy", "Honestly, I think this paper is overhyped.")
    out = classify_tweet_type(t)
    assert out == TweetType.OPINION
    assert t.tweet_type == TweetType.OPINION


def test_news_report_no_release_lang(known_handles_env):
    """reuters is in known_news_handles but the text has no release language."""
    t = _mk("reuters", "The Federal Reserve held interest rates steady at today's meeting.")
    out = classify_tweet_type(t)
    assert out == TweetType.NEWS_REPORT


def test_default_unknown(known_handles_env):
    """Unknown author + ambiguous text → UNKNOWN."""
    t = _mk("randomuser123", "going to the store later, want anything?")
    out = classify_tweet_type(t)
    assert out == TweetType.UNKNOWN


def test_first_person_hedge_marks_opinion(known_handles_env):
    """First-person hedging language → OPINION even for an unknown author."""
    t = _mk("randomuser123", "imo the new model is a clear improvement over the old one")
    out = classify_tweet_type(t)
    assert out == TweetType.OPINION
