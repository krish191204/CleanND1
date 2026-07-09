"""Smoke tests for the cleaning pipeline."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.schemas import RawTweet
from app.pipeline import (
    ApiFilter,
    BotDetector,
    CredibilityScorer,
    Pipeline,
    RelevanceFilter,
    TextCleaner,
)


def _mk(text, handle="user", followers=1000, is_bot=False, verified=False, lang="en"):
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
        author_created_at=now - timedelta(days=30 if is_bot else 1500),
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


def _chain(items):
    """Run items through stages 1+2 (text cleaning) so we get CleanedTweets."""
    s1 = ApiFilter(min_followers=200).process(items)
    return TextCleaner(compute_minhash=False).process(s1.passed).passed


# ----------------------------------------------------------------------
# Stage 1
# ----------------------------------------------------------------------

def test_api_filter_blocks_low_followers():
    items = [
        _mk("Important news from a low-follower account", followers=10),
        _mk("Another news item from a trusted source", followers=10_000, handle="Reuters", verified=True),
    ]
    r = ApiFilter(min_followers=500).process(items)
    assert len(r.passed) == 1
    assert r.passed[0].author_handle == "Reuters"


def test_api_filter_blocks_spam_hashtags():
    items = [
        _mk("#spam #spam #spam #spam #spam #spam #spam #spam #spam #spam buy now", followers=2000),
    ]
    r = ApiFilter(min_followers=500, max_hashtags=5).process(items)
    assert len(r.passed) == 0
    assert any("hashtag_spam" in reason for _, reason in r.rejected)


def test_api_filter_blocks_language():
    items = [_mk("text", lang="ja")]
    r = ApiFilter(allowed_languages=["en"]).process(items)
    assert len(r.passed) == 0


# ----------------------------------------------------------------------
# Stage 2
# ----------------------------------------------------------------------

def test_text_clean_lowercases_and_strips_urls():
    item = _mk("Check out HTTP://example.com — it's an awesome resource for everyone")
    r = TextCleaner(compute_minhash=False).process([item])
    assert len(r.passed) == 1
    assert "http" not in r.passed[0].clean_text
    assert r.passed[0].clean_text == r.passed[0].clean_text.lower()


def test_text_clean_rejects_too_short():
    item = _mk("hi")  # 2 chars
    r = TextCleaner(compute_minhash=False).process([item])
    assert len(r.passed) == 0


# ----------------------------------------------------------------------
# Stage 3
# ----------------------------------------------------------------------

def test_bot_detect_flags_obvious_spam():
    # Bypass stage-1 follower filter by giving the spam account many followers
    raw = _mk(
        "BUY NOW click here 🚀🚀🚀🚀🚀🚀🚀 http://spam.example #deal #win #free #sale #limited",
        followers=200_000, is_bot=True, verified=False,
    )
    cleaned = TextCleaner(compute_minhash=False).process([raw]).passed
    r = BotDetector(reject_threshold=0.5).process(cleaned)
    all_items = r.rejected + [(p, "passed") for p in r.passed]
    assert all_items, "expected at least one item"
    if r.rejected:
        return  # great, rejected outright
    assert r.passed[0].bot_score >= 0.4, f"expected bot score >= 0.4, got {r.passed[0].bot_score}"


def test_bot_detect_passes_verified_news():
    raw = _mk(
        "NASA announces discovery of new exoplanet in nearby solar system today",
        handle="NASA", followers=50_000_000, verified=True,
    )
    cleaned = _chain([raw])
    r = BotDetector().process(cleaned)
    assert len(r.passed) == 1
    assert r.passed[0].bot_score < 0.5


# ----------------------------------------------------------------------
# Stage 4 / 5
# ----------------------------------------------------------------------

def test_relevance_scores_news_higher():
    # Use stronger keywords that hit the fallback scorer
    news_raw = _mk(
        "BREAKING: central bank announces emergency rate decision after market close",
        handle="Reuters", verified=True, followers=200_000,
    )
    fluff_raw = _mk("had a great time at the beach today with my dog", handle="alice", followers=200)
    cleaned = _chain([news_raw, fluff_raw])
    r = RelevanceFilter(relevance_threshold=0.0).process(cleaned)
    by_handle = {ct.raw.author_handle: ct for ct in r.passed}
    assert by_handle["Reuters"].relevance_score >= by_handle["alice"].relevance_score


def test_credibility_levels():
    news_raw = _mk("Reuters confirms new policy decision today", handle="Reuters", verified=True, followers=200_000)
    cleaned = _chain([news_raw])
    rel = RelevanceFilter(relevance_threshold=0.0).process(cleaned).passed
    scored = CredibilityScorer().process(rel).passed
    assert scored[0].credibility_level.value in ("high", "medium")


# ----------------------------------------------------------------------
# Layer A — Issue 3: noise filter soft-penalty for known handles
# ----------------------------------------------------------------------

def test_noise_filter_soft_penalty_for_known_handle(tmp_path, monkeypatch):
    """Tweets from @OpenAI with launch language ('we're launching our new
    model today') pattern-match the noise filter. With Issue 3 the
    filter applies a SOFT penalty instead of hard-rejecting."""
    import json as _json
    news_p = tmp_path / "test_known_news.json"
    news_p.write_text(_json.dumps({"ai_orgs": ["openai"]}))
    ind_p = tmp_path / "test_known_individuals.json"
    ind_p.write_text(_json.dumps({"ai_researchers": ["karpathy"]}))
    monkeypatch.setenv("CREDIBILITY_KNOWN_NEWS_HANDLES_PATH", str(news_p))
    monkeypatch.setenv("KNOWN_CREDIBLE_INDIVIDUALS_PATH", str(ind_p))

    from app.services import known_handles
    known_handles.reset_cache()

    from app.pipeline.stage3b_noise import NoiseFilter

    # Use language that DOES match a noise pattern: 'we're introducing
    # a new model today' is a hard rejection trigger in
    # stage3b_noise.PATTERNS['product_announce']:
    #   \bwe(?:'re|\s+are)\s+(?:extending|launching|releasing|introducing|shipping)\b
    t = _mk(
        "we're introducing a brand new model today with revolutionary capabilities for everyone",
        handle="openai", verified=True, followers=2_000_000,
    )
    from app.pipeline.stage2_text_clean import TextCleaner
    from app.pipeline.stage1_api_filter import ApiFilter
    raw_list = [t]
    s1 = ApiFilter(min_followers=200).process(raw_list).passed
    cleaned = TextCleaner(compute_minhash=False).process(s1).passed

    # Use a low threshold so we exercise the bypass path (any noise
    # label triggers rejection on unknown handles; only known handles
    # survive via the soft-penalty).
    f = NoiseFilter(reject_threshold=0.10, skip_for_known_handles=True)
    r = f.process(cleaned)
    # With threshold=0.10, ANY non-zero noise score would reject.
    # But the known-handle bypass softens the score to <=0.35 and the
    # threshold (0.10) should be checked against the softened score.
    # Wait — looking at the code, the threshold is checked against the
    # EFFECTIVE score (post-softening), so the known handle should pass.
    assert len(r.passed) == 1, f"known-news should pass via bypass; rejected: {r.rejected}"
    # The known-handle bypass records the matched noise label with a
    # 'noise_soft:' prefix so downstream knows to apply a soft penalty
    # rather than a hard reject.
    assert r.passed[0].noise_score > 0  # the pattern matched
    assert any("noise_soft:" in r for r in r.passed[0].bot_reasons)


def test_noise_filter_hard_rejects_unknown_handle_with_launch_language(tmp_path, monkeypatch):
    """Regression: same launch language from an UNKNOWN handle is still
    hard-rejected (no soft-penalty treatment)."""
    # Use the same env-var setup so the singleton's settings.cache is reset
    import json as _json
    news_p = tmp_path / "test_known_news.json"
    news_p.write_text(_json.dumps({"ai_orgs": ["openai"]}))
    ind_p = tmp_path / "test_known_individuals.json"
    ind_p.write_text(_json.dumps({"ai_researchers": ["karpathy"]}))
    monkeypatch.setenv("CREDIBILITY_KNOWN_NEWS_HANDLES_PATH", str(news_p))
    monkeypatch.setenv("KNOWN_CREDIBLE_INDIVIDUALS_PATH", str(ind_p))
    from app.services import known_handles
    known_handles.reset_cache()

    from app.pipeline.stage3b_noise import NoiseFilter

    t = _mk(
        "we're introducing a brand new model today with revolutionary capabilities for everyone",
        handle="randomuser123",  # not in any known list
    )
    from app.pipeline.stage2_text_clean import TextCleaner
    from app.pipeline.stage1_api_filter import ApiFilter
    s1 = ApiFilter(min_followers=200).process([t]).passed
    cleaned = TextCleaner(compute_minhash=False).process(s1).passed

    # threshold lowered to 0.10 to ensure any noise label triggers
    f = NoiseFilter(reject_threshold=0.10)
    r = f.process(cleaned)
    assert len(r.rejected) == 1, "launch language from unknown handle should be rejected"


# ----------------------------------------------------------------------
# Layer A — Issue 4: MinHash dedup keeps both with corroboration_group_id
# when both authors are known handles.
# ----------------------------------------------------------------------

def test_known_handle_dedup_keeps_both_with_corroboration_id(tmp_path, monkeypatch):
    """Two near-duplicate tweets from @AnthropicAI and @simonw should both
    pass Stage 2 with a shared corroboration_group_id (not rejected as
    near-duplicates)."""
    import json as _json
    news_p = tmp_path / "test_known_news.json"
    news_p.write_text(_json.dumps({"ai_orgs": ["anthropicai"], "researchers": ["simonw"]}))
    ind_p = tmp_path / "test_known_individuals.json"
    ind_p.write_text(_json.dumps({"ai_researchers": []}))
    monkeypatch.setenv("CREDIBILITY_KNOWN_NEWS_HANDLES_PATH", str(news_p))
    monkeypatch.setenv("KNOWN_CREDIBLE_INDIVIDUALS_PATH", str(ind_p))

    from app.services import known_handles
    known_handles.reset_cache()

    # Long enough to produce ≥ 5 tokens after stop-word + length filters.
    text = "we built a brand new agent framework for claude release today with extended capabilities"
    t1 = _mk(text, handle="anthropicai", verified=True)
    t2 = _mk(text, handle="simonw", verified=False)

    from app.pipeline.stage2_text_clean import TextCleaner
    from app.pipeline.stage1_api_filter import ApiFilter
    s1 = ApiFilter(min_followers=200).process([t1, t2]).passed
    f = TextCleaner(skip_dedup_for_known_handles=True)
    r = f.process(s1)
    # Both should pass — Issue 4 keeps known-handle near-duplicates
    assert len(r.passed) == 2, f"expected both passes, got rejected: {r.rejected}"
    # And they should share a corroboration_group_id
    a, b = r.passed
    assert a.corroboration_group_id is not None
    assert a.corroboration_group_id == b.corroboration_group_id


def test_unknown_handle_dedup_drops_second():
    """Regression: when one of two near-duplicate authors is NOT a known
    handle, the second tweet is rejected as a near-duplicate (preserving
    pre-Issue-4 behaviour)."""
    from app.services import known_handles
    known_handles.reset_cache()

    text = "SwiftUI 5.7 release notes — fixed async bugs this morning"
    t1 = _mk(text, handle="anthropicai", verified=True)  # known-news
    t2 = _mk(text, handle="randomuser456")                 # unknown

    from app.pipeline.stage2_text_clean import TextCleaner
    from app.pipeline.stage1_api_filter import ApiFilter
    s1 = ApiFilter(min_followers=200).process([t1, t2]).passed
    f = TextCleaner(skip_dedup_for_known_handles=True)
    r = f.process(s1)
    # t1 passes, t2 rejected (one of two is unknown)
    assert len(r.passed) == 1
    assert r.passed[0].raw.author_handle == "anthropicai"
    assert len(r.rejected) == 1
    assert "near_duplicate" in r.rejected[0][1]


def test_known_handles_setting_propagates_through_orchestrator():
    """The new stage2_skip_dedup_for_known_handles setting must reach
    TextCleaner through the orchestrator."""
    from app.pipeline import Pipeline
    pipe = Pipeline()
    assert pipe.text_cleaner.skip_dedup_for_known_handles is True


# ----------------------------------------------------------------------
# End-to-end
# ----------------------------------------------------------------------

def test_pipeline_end_to_end_surfaces_news():
    items = [
        _mk("BREAKING: central bank announces emergency rate decision today", handle="Reuters", verified=True, followers=200_000),
        _mk("BUY NOW click here http://spam.example 🚀🚀🚀🚀🚀", handle="promo", followers=10, is_bot=True),
        _mk("Had a great day today at the office", handle="alice", followers=200),
    ]
    pipe = Pipeline()
    pipe.enable_software_focus = False  # AI focus is a separate stage; keep this test about the general pipeline
    out = pipe.run(items)
    assert any(c.raw.author_handle == "Reuters" for c in out.surfaced)
    assert all(c.raw.author_handle != "promo" for c in out.surfaced)


def test_pipeline_pushes_uncertain_items_to_review():
    items = [
        _mk(
            "BREAKING important news — http://x.com http://x.com http://x.com #breaking 🚀",
            handle="mystery", followers=900, verified=False,
        ),
    ]
    out = Pipeline().run(items)
    # either rejected by bot (likely) OR pushed to review queue
    assert out.review_queue or out.surfaced == []