"""Sub-agent A — test coverage additions.

Targets: public functions in app/services/cards.py and a few under-tested
helpers in the pipeline. Each test is designed to catch a real, plausible
regression if someone changes the production code. See IMPROVEMENT_LOG.md
for the iter-by-iter tracking.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

from app.models.schemas import (
    BotPrediction,
    CleanedTweet,
    CredibilityLevel,
    NewsCard,
    RawTweet,
    ScoredTweet,
    TweetType,
)


# ---------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------

def _mk_raw(text: str = "Breaking news story about new AI model release today",
            handle: str = "openai",
            verified: bool = True,
            followers: int = 100_000,
            urls: list[str] | None = None) -> RawTweet:
    return RawTweet(
        id=f"id_{hash(text + handle) & 0xFFFFFFFF:x}",
        text=text,
        author_id="1",
        author_handle=handle,
        author_display_name=handle.title(),
        author_followers=followers,
        author_following=min(followers, 500),
        author_verified=verified,
        author_created_at=datetime(2022, 1, 1, tzinfo=timezone.utc),
        author_profile_image_url=None,
        author_description="x",
        lang="en",
        created_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        hashtags=[], urls=urls or [], mentions=[], media=[],
        like_count=50, retweet_count=10, reply_count=0, quote_count=0,
    )


def _mk_scored(
    text: str,
    handle: str = "openai",
    cluster_id: str | None = None,
    credibility_reasons: list[str] | None = None,
    is_burst_event: bool = False,
    bot_score: float = 0.1,
    credibility_score: float = 0.8,
    credibility_level: CredibilityLevel = CredibilityLevel.HIGH,
    tweet_type: TweetType = TweetType.UNKNOWN,
) -> ScoredTweet:
    raw = _mk_raw(text=text, handle=handle)
    clean = CleanedTweet(
        raw=raw,
        clean_text=text.lower(),
        tokens=text.lower().split(),
        lemmas=text.lower().split(),
        minhash_signature=None,
        language="en",
        bot_score=bot_score,
        bot_label=BotPrediction.HUMAN if bot_score < 0.3 else BotPrediction.UNCERTAIN,
        is_burst_event=is_burst_event,
        cluster_id=cluster_id,
    )
    return ScoredTweet(
        raw=raw, clean=clean, embedding=None,
        bot_score=bot_score,
        bot_label=BotPrediction.HUMAN if bot_score < 0.3 else BotPrediction.UNCERTAIN,
        relevance_score=0.6,
        quality_score=0.6,
        credibility_score=credibility_score,
        credibility_level=credibility_level,
        credibility_reasons=credibility_reasons or [],
        final_score=0.5,
        passed_all_stages=True,
        tweet_type=tweet_type,
        is_clustered=bool(cluster_id),
    )


# =====================================================================
# cards.py — to_card (public, currently has no direct unit test)
# =====================================================================

def test_to_card_propagates_topic_id_from_cluster_id():
    """to_card() must surface `st.cluster_id` as `card.topic_id` so the
    /api/topics/{id}/tweets endpoint can re-find it. Regression: a
    prior commit (bb70643) explicitly fixed this — refactoring
    to_card() must not silently drop the wiring.

    Set `cluster_id` DIRECTLY on the ScoredTweet (the field `to_card`
    reads from) so the test exercises the actual production wiring.
    """
    from app.services.cards import to_card
    st = _mk_scored(
        "OpenAI announces brand new Claude model with breakthrough capabilities",
    )
    st.cluster_id = "fixed-cluster-uuid-aaaa"
    card = to_card(st)
    assert card.topic_id == "fixed-cluster-uuid-aaaa", (
        f"expected cluster_id propagated as topic_id, got {card.topic_id!r}"
    )


def test_to_card_includes_burst_event_in_why_shown():
    """When is_burst_event=True, to_card should flag the card as
    'trending_now'. A refactor that breaks this would silently lose the
    trending badge from the frontend."""
    from app.services.cards import to_card
    st = _mk_scored(
        "OpenAI announces new model release today with breakthrough capabilities",
        is_burst_event=True,
        credibility_reasons=["verified_account", "burst_event"],
    )
    card = to_card(st)
    assert "trending_now" in card.why_shown, (
        f"burst_event should appear as trending_now, got {card.why_shown!r}"
    )
    assert "co_corroborated_burst" in card.why_shown


def test_to_card_extracts_first_sentence_as_headline():
    """The dashboard renders `card.headline` as the title. to_card must
    split the cleaned text on sentence boundaries and use the first
    sentence (capped at 160 chars), not the raw whole tweet.

    The clean_text pipeline lowercases everything, so the headline will
    be lower-case. The split must occur at the period after 'today'.
    """
    from app.services.cards import to_card
    long_text = "OpenAI releases GPT-5 today. It's a major upgrade with new capabilities."
    st = _mk_scored(long_text, handle="openai")
    card = to_card(st)
    # First sentence only — must end at 'today.' with no second sentence
    assert card.headline.endswith("today."), (
        f"headline should be just the first sentence, got {card.headline!r}"
    )
    assert "major upgrade" not in card.headline, (
        f"headline must not include later sentences, got {card.headline!r}"
    )
    # And summary must include the rest
    assert "major upgrade" in card.summary


def test_to_card_falls_back_to_author_status_url():
    """When the tweet has no `urls`, to_card should construct a status
    URL from the author's handle + tweet id. A regression here would
    leave cards without a clickable source on the dashboard."""
    from app.services.cards import to_card
    raw = _mk_raw(
        text="OpenAI releases GPT-5 today with major capability improvements",
        handle="openai",
        urls=[],
    )
    # overwrite id deterministically
    object.__setattr__(raw, "id", "12345abcde")
    clean = CleanedTweet(
        raw=raw, clean_text=raw.text.lower(), tokens=raw.text.lower().split(),
        lemmas=raw.text.lower().split(), minhash_signature=None, language="en",
    )
    st = ScoredTweet(
        raw=raw, clean=clean, embedding=None,
        bot_score=0.1, bot_label=BotPrediction.HUMAN,
        credibility_score=0.8, credibility_level=CredibilityLevel.HIGH,
        final_score=0.5, passed_all_stages=True,
    )
    card = to_card(st)
    assert card.url == "https://x.com/openai/status/12345abcde", card.url


def test_credibility_color_for_all_levels():
    """credibility_color() must return a non-empty hex code for every
    CredibilityLevel. A missing level would crash the frontend's
    level→color mapping."""
    from app.services.cards import credibility_color
    for level in CredibilityLevel:
        hex_code = credibility_color(level)
        assert hex_code.startswith("#"), f"{level} returned {hex_code!r} (expected hex)"
        assert len(hex_code) == 7, f"{level} returned {hex_code!r} (want 7-char #RRGGBB)"


# =====================================================================
# stage3b_noise.py — credibility_penalty (public helper, untested)
# =====================================================================

def test_noise_credibility_penalty_tiers():
    """credibility_penalty is the function Stage 5 calls to demote a
    tweet's credibility based on its noise score. It has FOUR piecewise
    tiers (< 0.15, < 0.35, < 0.55, else). All boundaries must map to the
    right penalty or downstream credibility would shift unpredictably."""
    from app.pipeline.stage3b_noise import credibility_penalty
    # Below first threshold → no penalty
    assert credibility_penalty(0.00) == 0.0
    assert credibility_penalty(0.14) == 0.0
    # Second tier → 0.10
    assert credibility_penalty(0.15) == 0.10
    assert credibility_penalty(0.20) == 0.10
    assert credibility_penalty(0.34) == 0.10
    # Third tier → 0.20
    assert credibility_penalty(0.35) == 0.20
    assert credibility_penalty(0.50) == 0.20
    assert credibility_penalty(0.54) == 0.20
    # Top tier → 0.30 (capped, not unbounded)
    assert credibility_penalty(0.55) == 0.30
    assert credibility_penalty(0.75) == 0.30
    assert credibility_penalty(1.00) == 0.30


# =====================================================================
# stage5_credibility.py — _host_of + _load_known_news_handles
# =====================================================================

def test_credibility_host_of_strips_www_and_lowercases():
    """`_host_of` is used to match tweet URLs against the whitelist /
    blacklist. A buggy hostname extractor would cause the whitelist
    (reuters.com, nytimes.com, ...) to silently miss real domain hits —
    e.g. a 'www.nytimes.com' URL wouldn't match the 'nytimes.com' row."""
    from app.pipeline.stage5_credibility import CredibilityScorer
    assert CredibilityScorer._host_of("https://www.nytimes.com/article/123") == "nytimes.com"
    assert CredibilityScorer._host_of("https://api.openai.com/v1/models") == "api.openai.com"
    assert CredibilityScorer._host_of("https://Nytimes.COM/article") == "nytimes.com"
    # Garbage URL → empty string (no exception)
    assert CredibilityScorer._host_of("") == ""
    assert CredibilityScorer._host_of("not a url") == ""


def test_credibility_load_known_news_handles_dict_with_comments():
    """The loader supports two JSON shapes: a flat list and a dict with
    categorised groups (e.g. `ai_orgs`, `researchers`). Keys starting
    with `_` are comment keys and must be skipped. A bug here would
    either drop the news lists OR treat the comment as a category."""
    import tempfile
    from app.pipeline.stage5_credibility import CredibilityScorer
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "kn.json")
        with open(p, "w") as f:
            json.dump({
                "_comment": "this metadata key must be skipped",
                "ai_orgs": ["openai", "anthropic"],
                "researchers": ["karpathy"],
            }, f)
        handles = CredibilityScorer._load_known_news_handles(p)
    # Real handles must be present
    assert "openai" in handles
    assert "anthropic" in handles
    assert "karpathy" in handles
    # The comment category key must NOT have leaked in
    assert "_comment" not in handles
    assert len(handles) == 3


def test_credibility_load_known_news_handles_fallback_when_missing():
    """If the JSON file is missing entirely, the loader must fall back
    to the in-code `KNOWN_NEWS_HANDLES_FALLBACK` set — otherwise a
    fresh checkout with no data dir would have NO known-news handles
    and the +0.30 credibility boost would silently disappear."""
    import tempfile
    from app.pipeline.stage5_credibility import CredibilityScorer
    handles = CredibilityScorer._load_known_news_handles("/no/such/path.json")
    # Must contain the canonical wire-service handles from the fallback
    for required in ("reuters", "openai", "anthropicai", "nvidiaai"):
        assert required in handles, f"fallback missing {required!r}"
    # And it must NOT be empty
    assert len(handles) >= 10


# =====================================================================
# orchestrator.py — _uncertainty_margin
# =====================================================================

def test_orchestrator_uncertainty_margin_maxes_at_point_five():
    """`_uncertainty_margin` returns 1.0 when both bot_score and
    credibility_score are exactly 0.5 (max disagreement with either
    pole). It returns 0.0 when both are at the poles (bot=0 or 1 AND
    credibility=0 or 1). A mis-coded `2 * abs(bot-0.5)` formula
    instead of the correct `abs(bot-0.5) * 2` would invert these."""
    from app.pipeline.orchestrator import Pipeline
    pipe = Pipeline.__new__(Pipeline)  # skip __init__ wiring

    # Build a scored tweet with both at 0.5 → max margin
    mid = _mk_scored(
        "OpenAI releases GPT-5 with new capabilities today",
        bot_score=0.5, credibility_score=0.5,
    )
    margin = pipe._uncertainty_margin(mid)
    assert abs(margin - 1.0) < 1e-9, f"mid (0.5,0.5) should yield margin=1.0, got {margin}"

    # Both at poles → 0.0 margin (very confident)
    confident = _mk_scored(
        "OpenAI releases GPT-5 with new capabilities today",
        bot_score=0.0, credibility_score=1.0,
    )
    margin = pipe._uncertainty_margin(confident)
    assert abs(margin) < 1e-9, f"(0.0, 1.0) should yield margin=0.0, got {margin}"
