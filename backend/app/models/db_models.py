"""SQLAlchemy ORM models — for SQLite/Postgres persistence."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TweetORM(Base):
    """Persisted cleaned + scored tweet."""
    __tablename__ = "tweets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    author_id: Mapped[str] = mapped_column(String(64), index=True)
    author_handle: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text)
    clean_text: Mapped[str] = mapped_column(Text)
    lang: Mapped[Optional[str]] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    bot_score: Mapped[float] = mapped_column(Float, default=0.0)
    bot_label: Mapped[str] = mapped_column(String(32), default="uncertain")
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    credibility_score: Mapped[float] = mapped_column(Float, default=0.0)
    credibility_level: Mapped[str] = mapped_column(String(16), default="unverified")
    final_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    passed_all_stages: Mapped[bool] = mapped_column(Boolean, default=False)
    software_focus_passed: Mapped[bool] = mapped_column(Boolean, default=True)
    software_focus_meta: Mapped[list[str]] = mapped_column(JSON, default=list)

    embedding: Mapped[Optional[list[float]]] = mapped_column(JSON, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    __table_args__ = (
        Index("ix_tweets_passed_score", "passed_all_stages", "final_score"),
        Index("ix_tweets_created_bot", "created_at", "bot_label"),
        Index("ix_tweets_software_focus", "software_focus_passed"),
    )


class ReviewORM(Base):
    """Human-labeled review items."""
    __tablename__ = "reviews"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tweet_id: Mapped[str] = mapped_column(String(64), index=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)

    model_bot_score: Mapped[float] = mapped_column(Float)
    model_credibility: Mapped[float] = mapped_column(Float)
    model_relevance: Mapped[float] = mapped_column(Float)
    uncertainty_margin: Mapped[float] = mapped_column(Float, index=True)

    label: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    labeler_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    labeled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelMetricORM(Base):
    """Training & eval metrics tracked over time (for continuous improvement dashboard)."""
    __tablename__ = "model_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(32))
    metric_name: Mapped[str] = mapped_column(String(64))
    metric_value: Mapped[float] = mapped_column(Float)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    extras: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class FeedbackORM(Base):
    """Like/dislike / category feedback per tweet.

    Drives active learning: aggregates are fed into the nightly retrainer
    alongside human review labels.
    """
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tweet_id: Mapped[str] = mapped_column(String(64), index=True)
    signal: Mapped[str] = mapped_column(String(8), index=True)  # 'up' | 'down'
    category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)