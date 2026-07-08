"""Stage 5: Credibility scoring.

Combines:
- Domain reliability list (whitelisted/blacklisted domains)
- Propagation patterns (bot_score inverted + reverse-chronology novelty)
- Source verification (verified flag + followers + age)
- Cross-account corroboration (will be wired to burst-cluster later)

Output: 0..1 score, mapped to CredibilityLevel bands.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np

from ..config import get_settings
from ..models.schemas import CleanedTweet, CredibilityLevel, ScoredTweet
from .base import Stage, StageResult


class CredibilityScorer(Stage[CleanedTweet, ScoredTweet]):
    name = "stage5_credibility"

    DEFAULT_BLACKLIST = {
        "example-spam-site.com",
        "buy-followers-now.io",
    }
    DEFAULT_WHITELIST = {
        "reuters.com",
        "apnews.com",
        "bbc.co.uk",
        "nytimes.com",
        "theguardian.com",
        "washingtonpost.com",
        "bloomberg.com",
        "nasa.gov",
        "who.int",
        "europa.eu",
    }
    KNOWN_NEWS_HANDLES = {
        # Tier-1 wire services & major outlets
        "Reuters", "AP", "BBCBreaking", "BBCWorld", "nytimes", "washingtonpost",
        "WSJ", "CNN", "CNBC", "FT", "TheEconomist", "Bloomberg", "Nature",
        "ScienceMagazine", "WHO", "EUCommission", "BBCNews",
        # Tech / AI (when they post news, not products)
        "AnthropicAI", "OpenAI", "GoogleDeepMind", "NVIDIAAI", "googlegemma",
        # Defense / geopolitics
        "osinttechnical", "oryxspioenkop", "CaolanReports",
        # Markets / finance (specialist analysts with track records)
        "Hedgeye", "KobeissiLetter", "unusual_whales", "DeItaone",
        # Defense / gov
        "ZelenskyyUa", "BorisJohnson",
        # Note: NASA is intentionally NOT here — their 4th-of-July post isn't news.
        # Real NASA news still passes via bot_score + engagement scoring.
    }

    def __init__(
        self,
        whitelist: Iterable[str] | None = None,
        blacklist: Iterable[str] | None = None,
        known_handles: Iterable[str] | None = None,
        reject_below: float = 0.20,
    ) -> None:
        super().__init__()
        settings = get_settings()
        self.whitelist = set(whitelist or self.DEFAULT_WHITELIST)
        self.blacklist = set(blacklist or self.DEFAULT_BLACKLIST)
        self.known_handles = set(h or "" for h in (known_handles or self.KNOWN_NEWS_HANDLES))
        self.reject_below = reject_below
        self.high_t = settings.credibility_high_threshold
        self.medium_t = settings.credibility_medium_threshold

    # ------------------------------------------------------------------
    def process(self, items: list[CleanedTweet]) -> StageResult[ScoredTweet]:
        passed: list[ScoredTweet] = []
        rejected: list[tuple[ScoredTweet, str]] = []

        for ct in items:
            score, reasons = self._score(ct)
            # apply noise penalty if the noise stage flagged this tweet
            noise_score = getattr(ct, "noise_score", 0.0)
            if noise_score > 0:
                from .stage3b_noise import credibility_penalty

                pen = credibility_penalty(noise_score)
                if pen:
                    score -= pen
                    noise_labels = getattr(ct, "noise_labels", []) or []
                    for lbl in noise_labels:
                        reasons.append(f"noise:{lbl}")

            score = float(np.clip(score, 0.0, 1.0))
            level = self._level(score)
            reasons.extend(self._level_reasons(level))

            st = ScoredTweet(raw=ct.raw, clean=ct)
            st.credibility_score = score
            st.credibility_level = level
            st.credibility_reasons = reasons
            st.embedding = ct.embedding

            # Composite final score (used for ranking)
            st.final_score = self._composite(st)
            st.passed_all_stages = score >= self.reject_below

            if not st.passed_all_stages:
                rejected.append((st, f"credibility={score:.2f}"))
            else:
                passed.append(st)

        return StageResult(
            passed=passed,
            rejected=rejected,
            stats={"input": len(items), "passed": len(passed), "rejected": len(rejected)},
        )

    # ------------------------------------------------------------------
    def _score(self, ct: CleanedTweet) -> tuple[float, list[str]]:
        tw = ct.raw
        reasons: list[str] = []
        score = 0.5  # neutral default

        # 1. source verification
        if tw.author_verified:
            score += 0.15
            reasons.append("verified_account")
        if tw.author_handle in self.known_handles:
            score += 0.2
            reasons.append("known_news_handle")
        if tw.author_followers >= 50_000:
            score += 0.05
            reasons.append("large_following")

        # 2. domain reliability
        for url in tw.urls:
            host = self._host_of(url)
            if host in self.whitelist:
                score += 0.25
                reasons.append(f"whitelisted:{host}")
            if host in self.blacklist:
                score -= 0.5
                reasons.append(f"blacklisted:{host}")

        # 3. bot probability inversely correlated
        score -= 0.2 * ct.bot_score
        if ct.bot_score > 0.5:
            reasons.append(f"high_bot_score={ct.bot_score:.2f}")

        # 4. burst event bonus (corroboration signal)
        if ct.is_burst_event:
            score += 0.10
            reasons.append("burst_event")

        # 5. media present
        if tw.media:
            score += 0.05

        # 6. quality baseline
        score += 0.1 * ct.quality_score

        # 7. account age (older = more credible)
        if tw.author_created_at:
            from datetime import datetime, timezone

            age_days = (datetime.now(timezone.utc) - tw.author_created_at).days
            if age_days > 365 * 3:
                score += 0.05

        return float(np.clip(score, 0.0, 1.0)), reasons

    def _level(self, score: float) -> CredibilityLevel:
        if score >= self.high_t:
            return CredibilityLevel.HIGH
        if score >= self.medium_t:
            return CredibilityLevel.MEDIUM
        if score >= self.reject_below:
            return CredibilityLevel.LOW
        return CredibilityLevel.UNVERIFIED

    def _level_reasons(self, level: CredibilityLevel) -> list[str]:
        return {
            CredibilityLevel.HIGH: ["green:high_credibility"],
            CredibilityLevel.MEDIUM: ["yellow:medium_credibility"],
            CredibilityLevel.LOW: ["orange:low_credibility"],
            CredibilityLevel.UNVERIFIED: ["red:unverified"],
        }[level]

    def _composite(self, st: ScoredTweet) -> float:
        # weights tuned for ranking newsworthiness
        w_cred, w_rel, w_qual, w_bot = 0.45, 0.30, 0.15, 0.10
        return float(
            w_cred * st.credibility_score
            + w_rel * st.clean.relevance_score
            + w_qual * st.clean.quality_score
            + w_bot * (1.0 - st.clean.bot_score)
        )

    @staticmethod
    def _host_of(url: str) -> str:
        try:
            from urllib.parse import urlparse

            return (urlparse(url).hostname or "").replace("www.", "")
        except Exception:
            return ""