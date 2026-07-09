"""Tests for the topic clustering module (Layer B Addition 1)."""
from __future__ import annotations

import numpy as np
from datetime import datetime, timezone

import pytest

from app.models.schemas import (
    BotPrediction,
    CleanedTweet,
    CredibilityLevel,
    RawTweet,
    ScoredTweet,
)
from app.pipeline.topic_grouper import Cluster, cluster_tweets


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _mk_scored(text: str, handle: str, embedding: list[float] | None,
               final_score: float = 0.7) -> ScoredTweet:
    """Build a fully-populated ScoredTweet for the grouper."""
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
        raw=raw, clean=clean, embedding=embedding,
        bot_score=0.0, bot_label=BotPrediction.HUMAN,
        relevance_score=0.8, quality_score=0.6,
        credibility_score=0.8, credibility_level=CredibilityLevel.HIGH,
        final_score=final_score, passed_all_stages=True,
    )


def _vec(s: str) -> list[float]:
    """A deterministic 'embedding' from a string — 8-dim bag-of-words style.
    Same input → same output. Different inputs with shared tokens → similar
    vectors. We use this instead of a real sentence-transformer for tests."""
    tokens = s.lower().split()
    base = np.array([0.0] * 8, dtype=np.float32)
    for t in tokens:
        # simple hash -> a position
        h = sum(ord(c) for c in t) % 8
        base[h] += 1.0
    base /= max(np.linalg.norm(base), 1e-9)
    return base.tolist()


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------

def test_two_similar_tweets_cluster_together():
    """Two tweets with high token overlap → same cluster."""
    a = _mk_scored(
        "OpenAI releases new Claude model with breakthrough benchmarks",
        "anthropicai",
        _vec("claude release breakthrough benchmarks openai"),
    )
    b = _mk_scored(
        "Anthropic just released a new Claude model with breakthrough performance",
        "simonw",
        _vec("claude release breakthrough performance anthropic"),
    )
    clusters = cluster_tweets([a, b])
    assert len(clusters) == 1
    assert clusters[0].tweet_count == 2
    assert {a.raw.id, b.raw.id} == {t.raw.id for t in clusters[0].tweets}


def test_two_dissimilar_tweets_dont_cluster():
    """Two tweets with no token overlap → separate clusters (singletons)."""
    a = _mk_scored(
        "OpenAI releases new Claude model with breakthrough benchmarks",
        "anthropicai",
        _vec("claude release breakthrough benchmarks openai"),
    )
    b = _mk_scored(
        "Stock market reaches all time high on positive earnings",
        "reuters",
        _vec("stock market earnings high finance"),
    )
    clusters = cluster_tweets([a, b], distance_threshold=0.05)
    # With very tight threshold, no clustering happens
    assert len(clusters) == 2
    for c in clusters:
        assert c.tweet_count == 1


def test_singleton_unclustered():
    """Single tweet → returns singleton with empty label."""
    a = _mk_scored("just one tweet here", "anyone",
                   _vec("just one tweet here"))
    clusters = cluster_tweets([a])
    assert len(clusters) == 1
    assert clusters[0].tweet_count == 1
    assert clusters[0].label == ""  # too few tweets to generate TF-IDF


def test_label_for_ai_news_cluster():
    """Top TF-IDF terms for an AI-news cluster should include 'claude' or 'model'."""
    a = _mk_scored(
        "OpenAI releases new Claude model with breakthrough benchmarks",
        "anthropicai",
        _vec("claude release breakthrough benchmarks openai"),
    )
    b = _mk_scored(
        "Anthropic announces new Claude model for developers",
        "simonw",
        _vec("claude announce model developer anthropic"),
    )
    c = _mk_scored(
        "New Claude model released with extended capabilities today",
        "swyx",
        _vec("claude release model capability today"),
    )
    clusters = cluster_tweets([a, b, c], distance_threshold=0.5)
    assert len(clusters) == 1
    label = clusters[0].label
    assert label, "label should be non-empty for 3-tweet cluster"
    # The label should contain AI-related terms; check the cluster actually grouped.
    assert clusters[0].tweet_count == 3


def test_anchor_is_highest_scoring_member():
    """The cluster's anchor tweet is the one with the highest final_score."""
    a = _mk_scored("OpenAI releases Claude", "anthropicai",
                   _vec("claude release openai"), final_score=0.5)
    b = _mk_scored("Anthropic releases Claude", "simonw",
                   _vec("claude release anthropic"), final_score=0.9)  # highest
    c = _mk_scored("New Claude released", "swyx",
                   _vec("claude release new"), final_score=0.7)
    clusters = cluster_tweets([a, b, c], distance_threshold=0.5)
    assert len(clusters) == 1
    assert clusters[0].anchor.raw.author_handle == "simonw"
    assert clusters[0].anchor_tweet_id == b.raw.id


def test_cluster_id_is_stable_across_runs():
    """Same tweets → same cluster id (lets the DB upsert idempotently)."""
    tweets = [
        _mk_scored("claude released", "anthropicai", _vec("claude release")),
        _mk_scored("anthropic released claude", "simonw", _vec("anthropic release claude")),
    ]
    a = cluster_tweets(tweets, distance_threshold=0.5)
    b = cluster_tweets(tweets, distance_threshold=0.5)
    assert a[0].id == b[0].id
