"""Tests for the Software-scope Focus stage (Stage 0)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import get_settings
from app.models.schemas import RawTweet
from app.pipeline.stage_software_focus import (
    SoftwareFocusFilter,
    clean_tweet_for_software_focus,
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

KNOWN_JSON = {
    "researchers":   ["ylecun", "karpathy", "guido"],
    "practitioners": ["wesbos", "swyx"],
    "organizations": ["github", "anthropicai", "huggingface"],
    "papers":        ["arxiv", "paperswithcode"],
    "media":         ["theverge", "arstechnica"],
    "engineering_voices": ["rich_harris", "kelseyhightower"],
}


@pytest.fixture
def known_accounts_file(tmp_path) -> Path:
    p = tmp_path / "known_software.json"
    p.write_text(json.dumps(KNOWN_JSON))
    return p


@pytest.fixture
def f(known_accounts_file):
    return SoftwareFocusFilter(
        known_accounts_path=known_accounts_file,
        min_followers=100,
        min_account_age_days=30,
        min_engagement=5,
        require_all_signals=False,
    )


def _mk(
    *,
    text: str,
    handle: str = "alice",
    display_name: str = "Alice Dev",
    bio: str = "Software engineer | Rust + TypeScript",
    followers: int = 5000,
    verified: bool = False,
    age_days: int = 1000,
    likes: int = 50,
    retweets: int = 10,
):
    now = datetime.now(timezone.utc)
    return RawTweet(
        id=str(abs(hash(text + handle)) % (10**15)),
        text=text,
        author_id="1",
        author_handle=handle,
        author_display_name=display_name,
        author_followers=followers,
        author_following=min(followers, 500),
        author_verified=verified,
        author_created_at=now - timedelta(days=age_days),
        author_profile_image_url="https://x.com/x.jpg",
        author_description=bio,
        lang="en",
        created_at=now,
        hashtags=[w for w in text.split() if w.startswith("#")],
        urls=[w for w in text.split() if w.startswith("http")],
        mentions=[w for w in text.split() if w.startswith("@")],
        media=[],
        like_count=likes,
        retweet_count=retweets,
        reply_count=0,
        quote_count=0,
    )


# ---------------------------------------------------------------------
# Account-level
# ---------------------------------------------------------------------

def test_bio_with_ai_keyword_passes_account(f):
    t = _mk(text="new transformer paper out", bio="AI researcher at Meta", handle="alice")
    r = f.process([t])
    assert len(r.passed) == 1


def test_bio_with_programming_language_passes_account(f):
    """Expansion to software sphere: a Rust/TypeScript dev bio passes."""
    t = _mk(text="type release notes", bio="Software engineer | Rust + TypeScript", handle="alice")
    r = f.process([t])
    assert len(r.passed) == 1


def test_bio_with_framework_keyword_passes_account(f):
    t = _mk(text="new release", bio="Frontend engineer — React + Next.js", handle="alice")
    r = f.process([t])
    assert len(r.passed) == 1


def test_no_bio_no_display_name_no_known_handle_rejects(f):
    t = _mk(text="a new release", bio="", display_name="Bob")
    r = f.process([t])
    assert len(r.rejected) == 1
    assert r.rejected[0][1] == "account_not_software_focused"


def test_display_name_with_software_term_passes_account(f):
    t = _mk(text="new release", display_name="Staff Engineer Jane")
    r = f.process([t])
    assert len(r.passed) == 1


def test_known_handle_passes_regardless_of_bio(f):
    t = _mk(text="a new release", handle="rich_harris", bio="just a guy")
    r = f.process([t])
    assert len(r.passed) == 1


def test_low_followers_rejected(f):
    t = _mk(text="new transformer paper", followers=50)
    r = f.process([t])
    assert any(reason[1].startswith("followers<") for reason in r.rejected)


def test_new_account_rejected(f):
    t = _mk(text="new transformer paper", age_days=15)
    r = f.process([t])
    assert any(reason[1].startswith("account_age<") for reason in r.rejected)


# ---------------------------------------------------------------------
# Tweet content
# ---------------------------------------------------------------------

def test_tweet_with_ai_terms_passes_content(f):
    t = _mk(text="new paper on model training for diffusion models", handle="anthropicai")
    r = f.process([t])
    assert len(r.passed) == 1


def test_tweet_with_programming_terms_passes_content(f):
    """Software-sphere expansion: programming terms in tweets count."""
    t = _mk(text="excited to announce our new release with docker and kubernetes",
            handle="github")
    r = f.process([t])
    assert len(r.passed) == 1


def test_tweet_with_framework_terms_passes_content(f):
    t = _mk(text="next.js 15 released with full react server components support",
            handle="vercel")
    r = f.process([t])
    assert len(r.passed) == 1


def test_tweet_with_database_terms_passes_content(f):
    t = _mk(text="postgres 17 adds logical replication of partitioned tables",
            handle="postgres")
    r = f.process([t])
    assert len(r.passed) == 1


def test_tweet_with_release_terms_passes_content(f):
    t = _mk(text="rust 1.80 released — improvements to type inference",
            handle="rustlang")
    r = f.process([t])
    assert len(r.passed) == 1


def test_tweet_without_software_terms_rejects(f):
    t = _mk(text="just had a great coffee this morning", handle="rich_harris")
    r = f.process([t])
    assert any(reason[1] == "tweet_no_software_terms" for reason in r.rejected)


# ---------------------------------------------------------------------
# Scam / crypto
# ---------------------------------------------------------------------

def test_giveaway_rejected(f):
    t = _mk(text="crypto giveaway for all holders, airdrop now!",
            handle="anthropicai", display_name="AnthropicAI")
    r = f.process([t])
    assert any(reason[1] == "tweet_scam_terms" for reason in r.rejected)


def test_scam_beats_software_content(f):
    t = _mk(text="our new docker release — giveaway inside, airdrop!",
            handle="rich_harris")
    r = f.process([t])
    assert any(reason[1] == "tweet_scam_terms" for reason in r.rejected)


# ---------------------------------------------------------------------
# Retweets
# ---------------------------------------------------------------------

def test_rt_from_unknown_author_rejected(f):
    t = _mk(text="rt @random_user an amazing new release came out",
            handle="random_user")
    r = f.process([t])
    assert any(reason[1] == "rt_unknown_author" for reason in r.rejected)


def test_rt_from_known_account_passes(f):
    t = _mk(text="rt @rich_harris interesting new rust release")
    r = f.process([t])
    assert len(r.passed) == 1


# ---------------------------------------------------------------------
# Engagement
# ---------------------------------------------------------------------

def test_low_engagement_rejected(f):
    t = _mk(text="a new transformer paper released", likes=1, retweets=0, handle="rich_harris")
    r = f.process([t])
    assert any(reason[1].startswith("low_engagement<") for reason in r.rejected)


def test_sufficient_engagement_passes(f):
    t = _mk(text="a new transformer paper released", likes=50, retweets=10, handle="rich_harris")
    r = f.process([t])
    assert len(r.passed) == 1


# ---------------------------------------------------------------------
# Functional helper
# ---------------------------------------------------------------------

def test_clean_tweet_helper_returns_object_for_pass(monkeypatch, known_accounts_file):
    s = get_settings()
    monkeypatch.setattr(s, "software_known_accounts_path", str(known_accounts_file))
    s.software_known_accounts_path = str(known_accounts_file)
    get_settings.cache_clear()
    t = _mk(text="a new release", handle="rich_harris")
    out = clean_tweet_for_software_focus(t)
    assert out is not None
    assert out.author_handle == "rich_harris"


def test_clean_tweet_helper_returns_none_for_reject(monkeypatch, known_accounts_file):
    monkeypatch.setattr(
        get_settings(), "software_known_accounts_path", str(known_accounts_file)
    )
    get_settings.cache_clear()
    t = _mk(text="buy crypto", handle="rich_harris", bio="", display_name="Bob")
    out = clean_tweet_for_software_focus(t)
    assert out is None


# ---------------------------------------------------------------------
# Configuration toggles
# ---------------------------------------------------------------------

def test_require_all_signals_off_by_default(f):
    t = _mk(text="new release", bio="Software engineer")
    r = f.process([t])
    assert len(r.passed) == 1


def test_check_retweets_false_disables_rt_filter(known_accounts_file):
    f = SoftwareFocusFilter(
        known_accounts_path=known_accounts_file,
        min_followers=100,
        min_account_age_days=30,
        min_engagement=0,
        check_retweets=False,
    )
    t = _mk(text="rt @unknown_user a great release came out", handle="rich_harris")
    r = f.process([t])
    assert len(r.passed) == 1


def test_check_engagement_false_disables_low_engagement_filter(known_accounts_file):
    f = SoftwareFocusFilter(
        known_accounts_path=known_accounts_file,
        min_followers=100,
        min_account_age_days=30,
        check_engagement=False,
    )
    t = _mk(text="a new release", likes=0, retweets=0, handle="rich_harris")
    r = f.process([t])
    assert len(r.passed) == 1


# ---------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------

def test_pipeline_runs_without_software_focus(known_accounts_file):
    """Pipeline() default should respect software_focus_enabled setting."""
    from app.pipeline import Pipeline

    pipe = Pipeline(software_focus=SoftwareFocusFilter(
        known_accounts_path=known_accounts_file,
        check_profile_metadata=False,
        check_engagement=False,
    ))
    pipe.enable_software_focus = False
    items = [_mk(text="new release", handle="rich_harris")]
    out = pipe.run(items)
    assert out.stats.ingested == 1


def test_from_settings_classmethod(known_accounts_file):
    s = get_settings()
    s.software_known_accounts_path = str(known_accounts_file)
    get_settings.cache_clear()
    f = SoftwareFocusFilter.from_settings(get_settings())
    assert "rich_harris" in f.known_accounts
    assert "github" in f.known_accounts
    assert "arxiv" in f.known_accounts
    get_settings.cache_clear()
