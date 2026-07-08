"""Pipeline orchestrator: wires the 5 stages + active-learning gate."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

from loguru import logger

from ..config import get_settings
from ..models.schemas import (
    BotPrediction,
    CleanedTweet,
    CredibilityLevel,
    PipelineStats,
    RawTweet,
    ReviewItem,
    ScoredTweet,
)
from .base import StageResult
from .stage1_api_filter import ApiFilter
from .stage2_text_clean import TextCleaner
from .stage3_bot_detect import BotDetector
from .stage3b_noise import NoiseFilter, credibility_penalty
from .stage4_relevance import RelevanceFilter
from .stage5_credibility import CredibilityScorer
from .stage_software_focus import SoftwareFocusFilter


@dataclass
class PipelineOutput:
    surfaced: list[ScoredTweet]                # meets surface_min_credibility tier
    demoted: list[ScoredTweet] = field(default_factory=list)  # below floor, kept for review
    review_queue: list[ReviewItem] = field(default_factory=list)
    stats: PipelineStats = field(default_factory=PipelineStats)


class Pipeline:
    """Composed 5-stage cleaner + active-learning gate.

    Example::

        pipe = Pipeline()
        out = pipe.run(raw_tweets)
        for card in out.surfaced:
            ...
    """

    def __init__(
        self,
        api_filter: Optional[ApiFilter] = None,
        text_cleaner: Optional[TextCleaner] = None,
        bot_detector: Optional[BotDetector] = None,
        noise_filter: Optional[NoiseFilter] = None,
        relevance: Optional[RelevanceFilter] = None,
        credibility: Optional[CredibilityScorer] = None,
        software_focus: Optional[SoftwareFocusFilter] = None,
        margin_threshold: Optional[float] = None,
        review_batch_size: Optional[int] = None,
        surface_min_credibility: Optional[str] = None,
        enable_software_focus: Optional[bool] = None,
    ) -> None:
        s = get_settings()
        self.api_filter = api_filter or ApiFilter(min_followers=s.min_followers)
        self.text_cleaner = text_cleaner or TextCleaner()
        self.bot_detector = bot_detector or BotDetector()
        self.noise_filter = noise_filter or NoiseFilter(reject_threshold=s.noise_reject_threshold)
        self.relevance = relevance or RelevanceFilter()
        self.credibility = credibility or CredibilityScorer()
        self.software_focus = software_focus or SoftwareFocusFilter.from_settings(s)
        self.margin_threshold = margin_threshold if margin_threshold is not None else s.active_learning_margin_threshold
        self.review_batch_size = review_batch_size or s.review_queue_batch_size
        self.surface_min_credibility = surface_min_credibility or s.surface_min_credibility
        if enable_software_focus is None:
            self.enable_software_focus = s.software_focus_enabled
        else:
            self.enable_software_focus = bool(enable_software_focus)

    # ------------------------------------------------------------------
    def run(self, raw: Iterable[RawTweet]) -> PipelineOutput:
        items = list(raw)
        stats = PipelineStats(ingested=len(items))

        # ---- Stage 0 — Software focus (opt-in) ----
        if self.enable_software_focus:
            r0 = self.software_focus(items)
            stats.passed_software_focus = len(r0.passed)
            stats.rejected_software_focus = len(r0.rejected)
            r1_in = r0.passed
        else:
            r1_in = items

        # ---- Stage 1 ----
        r1 = self.api_filter(r1_in)
        stats.passed_api_filter = len(r1.passed)

        # ---- Stage 2 ----
        r2 = self.text_cleaner(r1.passed)
        stats.passed_cleaning = len(r2.passed)

        # ---- Stage 3 ----
        r3 = self.bot_detector(r2.passed)
        stats.passed_bot_filter = len(r3.passed)

        # ---- Stage 3.5 — Noise / opinion / engagement-bait ----
        r3b = self.noise_filter(r3.passed)

        # ---- Stage 4 ----
        r4 = self.relevance(r3b.passed)
        stats.passed_relevance = len(r4.passed)

        # ---- Stage 5 ----
        r5 = self.credibility(r4.passed)
        stats.passed_credibility = len(r5.passed)

        # Apply surface floor — items below `surface_min_credibility` are still
        # kept in DB / review queue but excluded from the surfaced feed.
        from ..models.schemas import CredibilityLevel
        level_order = {CredibilityLevel.UNVERIFIED: 0, CredibilityLevel.LOW: 1,
                       CredibilityLevel.MEDIUM: 2, CredibilityLevel.HIGH: 3}
        cutoff = level_order.get(CredibilityLevel(self.surface_min_credibility), 3)
        surfaced = [st for st in r5.passed if level_order[st.credibility_level] >= cutoff]
        demoted = [st for st in r5.passed if level_order[st.credibility_level] < cutoff]
        stats.surfaced = len(surfaced)
        stats.demoted = len(demoted)

        # ---- Active-learning gate ----
        review = self._select_for_review(r5.passed, r3.rejected, r5.rejected)
        stats.in_review_queue = len(review)

        # Optionally: also queue uncertain/medium-credibility items for review
        for st in r5.passed:
            if (
                st.credibility_level in (CredibilityLevel.LOW, CredibilityLevel.MEDIUM)
                and self._uncertainty_margin(st) > self.margin_threshold
            ):
                review.append(self._to_review_item(st))

        # cap the review batch size
        review = review[: self.review_batch_size]

        from datetime import datetime, timezone
        stats.last_run_at = datetime.now(timezone.utc)

        logger.info(
            f"pipeline done: ingested={stats.ingested} surfaced={stats.surfaced} "
            f"demoted={stats.demoted} review_queue={stats.in_review_queue}"
        )
        return PipelineOutput(
            surfaced=surfaced,                # for the dashboard
            demoted=demoted,                  # for the DB + review queue
            review_queue=review,
            stats=stats,
        )

    # ------------------------------------------------------------------
    def _select_for_review(
        self,
        passed: list[ScoredTweet],
        bot_rejected: list[tuple[CleanedTweet, str]],
        cred_rejected: list[tuple[ScoredTweet, str]],
    ) -> list[ReviewItem]:
        items: list[ReviewItem] = []

        # 1. anything rejected near the bot threshold (active-learning sweet spot)
        for ct, reason in bot_rejected:
            if 0.55 <= ct.bot_score <= 0.95:
                items.append(self._to_review_item_from_cleaned(ct, reason))

        # 2. anything rejected by credibility but with high relevance (borderline news)
        for st, reason in cred_rejected:
            if st.clean.relevance_score >= 0.6:
                items.append(self._to_review_item(st, reason=reason))

        # 3. all uncertain-band passes
        for st in passed:
            if st.clean.bot_label in (BotPrediction.UNCERTAIN,):
                items.append(self._to_review_item(st))

        return items

    # ------------------------------------------------------------------
    def _uncertainty_margin(self, st: ScoredTweet) -> float:
        # 1.0 = model very unsure, 0.0 = model very sure
        bot_uncertainty = 1.0 - abs(st.clean.bot_score - 0.5) * 2
        cred_uncertainty = 1.0 - abs(st.credibility_score - 0.5) * 2
        return 0.5 * bot_uncertainty + 0.5 * cred_uncertainty

    def _to_review_item(
        self, st: ScoredTweet, reason: str = ""
    ) -> ReviewItem:
        snap = st.model_dump(mode="json")
        return ReviewItem(
            tweet_id=st.raw.id,
            snapshot=snap,
            model_bot_score=st.clean.bot_score,
            model_credibility=st.credibility_score,
            model_relevance=st.clean.relevance_score,
            uncertainty_margin=self._uncertainty_margin(st),
            category=None,
            notes=reason or None,
        )

    def _to_review_item_from_cleaned(
        self, ct: CleanedTweet, reason: str = ""
    ) -> ReviewItem:
        # Build a placeholder ScoredTweet-shaped dict for the snapshot
        snap = {
            "raw": ct.raw.model_dump(mode="json"),
            "clean": ct.model_dump(mode="json"),
            "bot_score": ct.bot_score,
            "credibility_score": 0.0,
            "relevance_score": 0.0,
        }
        return ReviewItem(
            tweet_id=ct.raw.id,
            snapshot=snap,
            model_bot_score=ct.bot_score,
            model_credibility=0.0,
            model_relevance=0.0,
            uncertainty_margin=1.0 - abs(ct.bot_score - 0.5) * 2,
            notes=reason or None,
        )