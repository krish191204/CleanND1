"""Stage 1: API-level filtering.

Catches the cheap-and-easy filters before we even bother cleaning text.
Goal: cut ingestion volume by 80%+ on the way in.

Rejection rules:
- min followers
- min account age
- not verified AND below stricter follower threshold (allow non-verified but require minimum trust)
- max hashtags / URLs in raw text (spam signal)
- language mismatch
- text length out of bounds (empty, way too long)
- retweets / quote-only / reply-only (configurable)
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable

from ..models.schemas import RawTweet
from .base import Stage, StageResult


_HASHTAG_RE = re.compile(r"#\w+")
_URL_RE = re.compile(r"https?://\S+|t\.co/\S+")
_MENTION_RE = re.compile(r"@\w+")


class ApiFilter(Stage[RawTweet, RawTweet]):
    """Lightweight filter applied to raw tweets from the API."""

    name = "stage1_api_filter"

    def __init__(
        self,
        min_followers: int = 500,
        min_account_age_days: int = 30,
        max_hashtags: int = 5,
        max_urls: int = 2,
        min_text_length: int = 20,
        max_text_length: int = 1000,
        allowed_languages: Iterable[str] = ("en", "es", "fr", "de", "pt"),
        drop_retweets: bool = False,
        drop_quotes_only: bool = True,
    ) -> None:
        super().__init__(
            min_followers=min_followers,
            min_account_age_days=min_account_age_days,
            max_hashtags=max_hashtags,
            max_urls=max_urls,
            min_text_length=min_text_length,
            max_text_length=max_text_length,
            allowed_languages=tuple(allowed_languages),
            drop_retweets=drop_retweets,
            drop_quotes_only=drop_quotes_only,
        )
        self.min_followers = min_followers
        self.min_account_age_days = min_account_age_days
        self.max_hashtags = max_hashtags
        self.max_urls = max_urls
        self.min_text_length = min_text_length
        self.max_text_length = max_text_length
        self.allowed_languages = set(allowed_languages)
        self.drop_retweets = drop_retweets
        self.drop_quotes_only = drop_quotes_only

    def process(self, items: list[RawTweet]) -> StageResult[RawTweet]:
        passed: list[RawTweet] = []
        rejected: list[tuple[RawTweet, str]] = []

        now = datetime.now(timezone.utc)
        for tw in items:
            reason = self._reject_reason(tw, now)
            if reason:
                rejected.append((tw, reason))
            else:
                passed.append(tw)

        return StageResult(
            passed=passed,
            rejected=rejected,
            stats={"input": len(items), "passed": len(passed), "rejected": len(rejected)},
        )

    # ------------------------------------------------------------------
    def _reject_reason(self, tw: RawTweet, now: datetime) -> str | None:
        # 1. language
        if tw.lang and tw.lang not in self.allowed_languages:
            return f"lang={tw.lang}"

        # 2. text length
        if len(tw.text) < self.min_text_length:
            return "text_too_short"
        if len(tw.text) > self.max_text_length:
            return "text_too_long"

        # 3. follower count
        if tw.author_followers < self.min_followers:
            return f"followers<{self.min_followers}"

        # 4. account age
        if tw.author_created_at:
            age = (now - tw.author_created_at).days
            if age < self.min_account_age_days:
                return f"account_age<{self.min_account_age_days}d"

        # 5. hashtag / URL spam heuristics on raw text
        if len(_HASHTAG_RE.findall(tw.text)) > self.max_hashtags:
            return "hashtag_spam"
        if len(_URL_RE.findall(tw.text)) > self.max_urls:
            return "url_spam"

        # 6. retweets
        if self.drop_retweets and tw.text.lstrip().lower().startswith("rt "):
            return "retweet"

        # 7. quote-only (starts with quoted tweet)
        if self.drop_quotes_only and tw.text.lstrip().lower().startswith(("q ", "qt ")):
            return "quote_only"

        return None