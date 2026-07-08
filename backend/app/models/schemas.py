"""Pydantic schemas - the public contract for the pipeline and API."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid4().hex


# =====================================================================
# Enums
# =====================================================================

class CredibilityLevel(str, Enum):
    HIGH = "high"          # green
    MEDIUM = "medium"      # yellow
    LOW = "low"            # orange
    UNVERIFIED = "unverified"  # red/gray


class BotPrediction(str, Enum):
    HUMAN = "human"
    LIKELY_HUMAN = "likely_human"
    UNCERTAIN = "uncertain"
    LIKELY_BOT = "likely_bot"
    BOT = "bot"


class ReviewLabel(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_MORE_INFO = "needs_more_info"


# =====================================================================
# Pipeline input/output schemas
# =====================================================================

class RawTweet(BaseModel):
    """Stage 0: tweet as it comes out of the API client."""
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=_new_id)
    text: str
    author_id: str
    author_handle: str
    author_display_name: str = ""
    author_followers: int = 0
    author_following: int = 0
    author_verified: bool = False
    author_created_at: Optional[datetime] = None
    author_profile_image_url: Optional[str] = None
    author_description: Optional[str] = None
    lang: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    hashtags: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    mentions: list[str] = Field(default_factory=list)
    media: list[str] = Field(default_factory=list)  # media urls
    like_count: int = 0
    retweet_count: int = 0
    reply_count: int = 0
    quote_count: int = 0
    source: Optional[str] = None
    raw: dict[str, Any] = Field(default_factory=dict)


class CleanedTweet(BaseModel):
    """Stage 2 output: normalized text + tokenized form + dedup signature.

    Stages 3 and 4 attach bot/relevance/quality attributes to this same object
    so downstream stages don't have to re-thread references.
    """
    model_config = ConfigDict(extra="ignore")

    raw: RawTweet
    clean_text: str
    tokens: list[str]
    lemmas: list[str]
    minhash_signature: Optional[bytes] = None
    language: str = "und"

    # Attached by Stage 3 (BotDetector)
    bot_score: float = 0.0
    bot_label: BotPrediction = BotPrediction.UNCERTAIN
    bot_reasons: list[str] = Field(default_factory=list)

    # Attached by Stage 4 (RelevanceFilter)
    relevance_score: float = 0.0
    quality_score: float = 0.0
    is_burst_event: bool = False
    cluster_id: Optional[str] = None
    embedding: Optional[list[float]] = None

    # Attached by Stage 3.5 (NoiseFilter) — opinion / engagement-bait / promo
    noise_score: float = 0.0
    noise_labels: list[str] = Field(default_factory=list)

    # Attached by Stage 0 (SoftwareFocusFilter)
    software_focus_passed: bool = True
    software_focus_meta: list[str] = Field(default_factory=list)

    @field_validator("bot_score", "relevance_score", "quality_score")
    @classmethod
    def _clip_0_1(cls, v: float) -> float:
        if v < 0.0:
            return 0.0
        if v > 1.0:
            return 1.0
        return v


class ScoredTweet(BaseModel):
    """Final output of the pipeline: ready for the dashboard."""
    raw: RawTweet
    clean: CleanedTweet
    embedding: Optional[list[float]] = None

    # Bot / spam signals (mirrored from clean.* for convenience)
    bot_score: float = 0.0           # 0 = human, 1 = bot
    bot_label: BotPrediction = BotPrediction.UNCERTAIN
    bot_reasons: list[str] = Field(default_factory=list)

    # Quality / relevance (mirrored from clean.*)
    relevance_score: float = 0.0     # 0..1
    quality_score: float = 0.0       # 0..1
    is_burst_event: bool = False
    cluster_id: Optional[str] = None

    # Credibility (added by Stage 5)
    credibility_score: float = 0.0   # 0..1
    credibility_level: CredibilityLevel = CredibilityLevel.UNVERIFIED
    credibility_reasons: list[str] = Field(default_factory=list)

    # Final composite "should show" decision
    final_score: float = 0.0         # weighted composite for ranking
    passed_all_stages: bool = False

    # Bookkeeping
    pipeline_version: str = "0.1.0"
    processed_at: datetime = Field(default_factory=_utcnow)

    @field_validator("bot_score", "relevance_score", "quality_score",
                     "credibility_score", "final_score")
    @classmethod
    def _clip_0_1(cls, v: float) -> float:
        if v < 0.0:
            return 0.0
        if v > 1.0:
            return 1.0
        return v


# =====================================================================
# Review queue
# =====================================================================

class ReviewItem(BaseModel):
    id: str = Field(default_factory=_new_id)
    tweet_id: str
    snapshot: dict[str, Any]                  # serialized ScoredTweet
    model_bot_score: float
    model_credibility: float
    model_relevance: float
    uncertainty_margin: float                # active-learning signal
    label: Optional[ReviewLabel] = None
    labeler_id: Optional[str] = None
    labeled_at: Optional[datetime] = None
    category: Optional[str] = None           # e.g. "sports", "politics"
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


# =====================================================================
# Frontend-facing DTOs
# =====================================================================

class NewsCard(BaseModel):
    """Lean DTO sent to the frontend dashboard."""
    id: str
    headline: str
    summary: str
    handle: str
    display_name: str
    profile_image_url: Optional[str] = None
    verified: bool
    timestamp: datetime
    media: list[str] = Field(default_factory=list)
    credibility_level: CredibilityLevel
    credibility_score: float
    human_verified: bool = False
    why_shown: list[str] = Field(default_factory=list)
    url: str = ""


class PipelineStats(BaseModel):
    ingested: int = 0
    passed_software_focus: int = 0
    rejected_software_focus: int = 0
    passed_api_filter: int = 0
    passed_cleaning: int = 0
    passed_bot_filter: int = 0
    passed_relevance: int = 0
    passed_credibility: int = 0
    surfaced: int = 0
    demoted: int = 0
    in_review_queue: int = 0
    last_run_at: Optional[datetime] = None