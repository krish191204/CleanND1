"""Known-handles cache (singleton).

Loads two whitelists from JSON once and exposes simple membership checks:

  - `is_known_news(handle)`       — in data/known_news_handles.json
  - `is_known_software(handle)`  — in data/known_software_accounts.json
  - `is_known_any(handle)`       — either list

The files are loaded via @functools.cache so they're parsed once per process
and survive pytest fixture resets via `reset_cache()`. Earlier in the codebase
these lists were loaded independently in stage_software_focus.py and
stage5_credibility.py — this singleton deduplicates that work and gives later
stages (stage2 dedup, stage3.5 noise, stage4 burst) a single place to check
"is this tweet from a handle we've curated as important?"

Path resolution: settings are the source of truth. `credibility_known_news_handles_path`
and `software_known_accounts_path` come from `app/config.py:Settings` (env-overridable).
We lazily import get_settings() inside `_resolve_path` so settings can be reloaded
in tests without breaking the singleton.
"""
from __future__ import annotations

import functools
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _load_set(path: str | Path, log_label: str) -> frozenset[str]:
    """Parse a handle-whitelist JSON file and return a frozenset of lowercase
    handles (without leading `@`). Supports both shapes:

      - top-level list: ["openai", "anthropicai", ...]
      - categorised dict: {"ai_orgs": [...], "researchers": [...], ...}
    """
    try:
        p = Path(path)
        if not p.exists():
            logger.warning(f"[{log_label}] not found: {path}")
            return frozenset()
        raw = p.read_text()
        data = json.loads(raw)
    except Exception as e:
        logger.warning(f"[{log_label}] failed to read {path}: {e}")
        return frozenset()

    out: set[str] = set()
    if isinstance(data, list):
        for h in data:
            if isinstance(h, str) and h:
                out.add(h.lower().lstrip("@"))
    elif isinstance(data, dict):
        for k, v in data.items():
            if k.startswith("_"):
                continue  # comment / metadata
            if isinstance(v, list):
                for h in v:
                    if isinstance(h, str) and h:
                        out.add(h.lower().lstrip("@"))
    return frozenset(out)


def _resolve_paths() -> tuple[str, str]:
    """Read both file paths from Settings. Lazy import avoids a circular
    dep on app.config (which is fine in practice — config.py doesn't
    import services)."""
    from ..config import get_settings

    s = get_settings()
    return s.credibility_known_news_handles_path, s.software_known_accounts_path


def _resolve_paths() -> tuple[str, str]:
    """Read both file paths from Settings. Lazy import avoids a circular
    dep on app.config (which is fine in practice — config.py doesn't
    import services)."""
    from ..config import get_settings

    s = get_settings()
    return s.credibility_known_news_handles_path, s.software_known_accounts_path


@functools.cache
def _news_handles() -> frozenset[str]:
    news_path, _, _ = _resolve_paths()
    return _load_set(news_path, "known_news")


@functools.cache
def _software_handles() -> frozenset[str]:
    _, software_path, _ = _resolve_paths()
    return _load_set(software_path, "known_software")


@functools.cache
def _individual_handles() -> frozenset[str]:
    _, _, individuals_path = _resolve_paths()
    return _load_set(individuals_path, "known_individuals")


def _resolve_paths() -> tuple[str, str, str]:
    """Read three file paths.

    Reads directly from `os.environ` rather than from the cached Settings
    singleton so that test monkeypatch-setenv() calls take effect on the
    next singleton read. Settings are still the
    .env-file-driven default; this function prefers env-overrides when
    present."""
    # Try env-direct first (test-friendly), fall back to the project-root
    # defaults that match the layout in `backend/data/*.json`.
    news = os.environ.get("CREDIBILITY_KNOWN_NEWS_HANDLES_PATH")
    software = os.environ.get("SOFTWARE_KNOWN_ACCOUNTS_PATH")
    individuals = os.environ.get("KNOWN_CREDIBLE_INDIVIDUALS_PATH")
    if news and software and individuals:
        return news, software, individuals
    # Fall back: use the Settings singleton for project-defaults. The
    # presence of any one missing env var triggers fallback (covers
    # production where .env is loaded once).
    from ..config import get_settings

    s = get_settings()
    return (
        news or s.credibility_known_news_handles_path,
        software or s.software_known_accounts_path,
        individuals or s.known_credible_individuals_path,
    )


def is_known_news(handle: str | None) -> bool:
    """True if `handle` (case-insensitive, leading @ tolerated) is in
    data/known_news_handles.json."""
    if not handle:
        return False
    return handle.lower().lstrip("@") in _news_handles()


def is_known_software(handle: str | None) -> bool:
    """True if `handle` is in data/known_software_accounts.json."""
    if not handle:
        return False
    return handle.lower().lstrip("@") in _software_handles()


def is_known_individual(handle: str | None) -> bool:
    """True if `handle` is in data/known_credible_individuals.json.

    Used to (a) bypass Stage 0/3/3.5 for prominent individual voices
    whose opinions are valuable, (b) classify their tweets as OPINION
    in the tweet_type classifier. Conservative: only authors in the
    curated JSON file are considered known individuals."""
    if not handle:
        return False
    return handle.lower().lstrip("@") in _individual_handles()


def is_known_any(handle: str | None) -> bool:
    """True if `handle` is in any of the three lists."""
    return (
        is_known_news(handle)
        or is_known_software(handle)
        or is_known_individual(handle)
    )


def known_news_handles() -> frozenset[str]:
    """All known-news handles (frozenset, lowercase, no @). Useful for
    building `from:handle OR from:handle...` queries upstream."""
    return _news_handles()


def known_software_handles() -> frozenset[str]:
    """All known-software handles (frozenset, lowercase, no @)."""
    return _software_handles()


def known_individual_handles() -> frozenset[str]:
    """All known-individual handles (frozenset, lowercase, no @)."""
    return _individual_handles()


def reset_cache() -> None:
    """Test helper — re-read all three JSON files on the next access.

    Call from a pytest fixture or monkeypatched site when you've written a
    new file and want the singleton to pick it up.
    """
    _news_handles.cache_clear()
    _software_handles.cache_clear()
    _individual_handles.cache_clear()
