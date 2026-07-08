"""SQLAlchemy database wrapper (SQLite/Postgres)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional

from loguru import logger
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings
from ..models.db_models import Base, FeedbackORM, ModelMetricORM, ReviewORM, TweetORM


def _json_default(o):
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def _json_safe(obj):
    """Recursively convert datetime objects to ISO strings."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


class Database:
    """Thin wrapper over SQLAlchemy — easy to swap implementations."""

    def __init__(self, url: Optional[str] = None) -> None:
        s = get_settings()
        self.url = url or s.database_url
        connect_args = {"check_same_thread": False} if self.url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(
            self.url, future=True, connect_args=connect_args, echo=False
        )
        self.SessionLocal = sessionmaker(
            bind=self.engine, autoflush=False, autocommit=False, future=True
        )

    def init(self) -> None:
        Base.metadata.create_all(self.engine)
        logger.info(f"db initialized at {self.url}")

    @contextmanager
    def session(self) -> Iterator[Session]:
        sess = self.SessionLocal()
        try:
            yield sess
            sess.commit()
        except Exception:
            sess.rollback()
            raise
        finally:
            sess.close()

    # ----- Tweets -----
    def upsert_tweet(self, t: dict) -> None:
        with self.session() as s:
            orm = s.get(TweetORM, t["id"])
            if orm is None:
                orm = TweetORM(id=t["id"])
                s.add(orm)
            orm.author_id = t.get("author_id", "")
            orm.author_handle = t.get("author_handle", "")
            orm.text = t.get("text", "")
            orm.clean_text = t.get("clean_text", "")
            orm.lang = t.get("lang")
            orm.created_at = t.get("created_at", datetime.utcnow())
            orm.processed_at = t.get("processed_at", datetime.utcnow())
            orm.bot_score = float(t.get("bot_score", 0.0))
            orm.bot_label = t.get("bot_label", "uncertain")
            orm.relevance_score = float(t.get("relevance_score", 0.0))
            orm.quality_score = float(t.get("quality_score", 0.0))
            orm.credibility_score = float(t.get("credibility_score", 0.0))
            orm.credibility_level = t.get("credibility_level", "unverified")
            orm.final_score = float(t.get("final_score", 0.0))
            orm.passed_all_stages = bool(t.get("passed_all_stages", False))
            orm.software_focus_passed = bool(t.get("software_focus_passed", True))
            orm.software_focus_meta = list(t.get("software_focus_meta") or [])
            orm.embedding = t.get("embedding")
            orm.payload = t.get("payload", {})

    def get_surfaced(
        self,
        limit: int = 50,
        offset: int = 0,
        min_credibility: float = 0.0,
        handle: Optional[str] = None,
        human_verified: Optional[bool] = None,
    ) -> list[dict]:
        with self.session() as s:
            stmt = (
                select(TweetORM)
                .where(TweetORM.passed_all_stages.is_(True))
                .where(TweetORM.credibility_score >= min_credibility)
                .order_by(TweetORM.final_score.desc())
            )
            if handle:
                stmt = stmt.where(TweetORM.author_handle == handle)
            stmt = stmt.limit(limit).offset(offset)
            return [self._serialize_tweet(orm) for orm in s.execute(stmt).scalars()]

    def get_one(self, tweet_id: str) -> Optional[dict]:
        with self.session() as s:
            orm = s.get(TweetORM, tweet_id)
            return self._serialize_tweet(orm) if orm else None

    @staticmethod
    def _serialize_tweet(orm: "TweetORM") -> dict:
        return {
            "id": orm.id,
            "author_id": orm.author_id,
            "author_handle": orm.author_handle,
            "author_display_name": "",
            "author_followers": 0,
            "author_verified": False,
            "author_profile_image_url": None,
            "author_description": None,
            "lang": orm.lang,
            "text": orm.text,
            "clean_text": orm.clean_text,
            "created_at": orm.created_at,
            "processed_at": orm.processed_at,
            "hashtags": [],
            "urls": [],
            "mentions": [],
            "media": [],
            "like_count": 0,
            "retweet_count": 0,
            "reply_count": 0,
            "quote_count": 0,
            "bot_score": orm.bot_score,
            "bot_label": orm.bot_label,
            "relevance_score": orm.relevance_score,
            "quality_score": orm.quality_score,
            "credibility_score": orm.credibility_score,
            "credibility_level": orm.credibility_level,
            "final_score": orm.final_score,
            "passed_all_stages": orm.passed_all_stages,
            "software_focus_passed": orm.software_focus_passed,
            "software_focus_meta": list(orm.software_focus_meta or []),
            "embedding": orm.embedding,
            "payload": orm.payload or {},
        }

    # ----- Reviews -----
    def queue_review(self, item: dict) -> None:
        with self.session() as s:
            existing = s.get(ReviewORM, item["id"])
            if existing is not None:
                return  # don't double-queue
            orm = ReviewORM(id=item["id"], **{
                k: v for k, v in item.items() if k != "id"
            })
            s.add(orm)

    def next_reviews(self, n: int = 25) -> list[ReviewORM]:
        with self.session() as s:
            stmt = (
                select(ReviewORM)
                .where(ReviewORM.label.is_(None))
                .order_by(ReviewORM.uncertainty_margin.desc())
                .limit(n)
            )
            return list(s.execute(stmt).scalars())

    def next_reviews_unlabeled(self, n: int = 25) -> list[dict]:
        """Return serialized unlabeled review items, scoped to the session."""
        out: list[dict] = []
        with self.session() as s:
            stmt = (
                select(ReviewORM)
                .where(ReviewORM.label.is_(None))
                .order_by(ReviewORM.uncertainty_margin.desc())
                .limit(n)
            )
            for r in s.execute(stmt).scalars():
                out.append({
                    "id": r.id,
                    "tweet_id": r.tweet_id,
                    "snapshot": r.snapshot,
                    "model_bot_score": r.model_bot_score,
                    "model_credibility": r.model_credibility,
                    "model_relevance": r.model_relevance,
                    "uncertainty_margin": r.uncertainty_margin,
                    "label": r.label,
                    "category": r.category,
                    "notes": r.notes,
                    "labeler_id": r.labeler_id,
                    "labeled_at": r.labeled_at.isoformat() if r.labeled_at else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                })
        return out

    def review_stats_aggregate(self) -> dict:
        with self.session() as s:
            from sqlalchemy import func
            total = s.execute(select(func.count(ReviewORM.id))).scalar() or 0
            labeled = (
                s.execute(
                    select(func.count(ReviewORM.id)).where(ReviewORM.label.is_not(None))
                ).scalar()
                or 0
            )
            approved = (
                s.execute(
                    select(func.count(ReviewORM.id)).where(ReviewORM.label == "approved")
                ).scalar()
                or 0
            )
            rejected = (
                s.execute(
                    select(func.count(ReviewORM.id)).where(ReviewORM.label == "rejected")
                ).scalar()
                or 0
            )
        return {
            "total": total,
            "labeled": labeled,
            "unlabeled": total - labeled,
            "approved": approved,
            "rejected": rejected,
        }

    def all_reviews(self, limit: int = 100, only_labeled: bool = False) -> list[ReviewORM]:
        with self.session() as s:
            stmt = select(ReviewORM).order_by(ReviewORM.created_at.desc()).limit(limit)
            if only_labeled:
                stmt = stmt.where(ReviewORM.label.is_not(None))
            return list(s.execute(stmt).scalars())

    def label_review(
        self,
        review_id: str,
        label: str,
        category: Optional[str] = None,
        notes: Optional[str] = None,
        labeler_id: Optional[str] = None,
    ) -> None:
        with self.session() as s:
            orm = s.get(ReviewORM, review_id)
            if orm is None:
                return
            orm.label = label
            orm.category = category
            orm.notes = notes
            orm.labeler_id = labeler_id
            orm.labeled_at = datetime.utcnow()

    def labeled_reviews_for_training(self) -> list[dict]:
        with self.session() as s:
            stmt = select(ReviewORM).where(ReviewORM.label.is_not(None))
            return [
                {
                    "tweet_id": r.tweet_id,
                    "label": r.label,
                    "category": r.category,
                    "snapshot": r.snapshot,
                    "bot_score": r.model_bot_score,
                    "credibility": r.model_credibility,
                }
                for r in s.execute(stmt).scalars()
            ]

    # ----- Metrics -----
    def record_metric(
        self,
        model_name: str,
        version: str,
        metric_name: str,
        value: float,
        sample_size: int = 0,
        extras: Optional[dict] = None,
    ) -> None:
        with self.session() as s:
            orm = ModelMetricORM(
                model_name=model_name,
                version=version,
                metric_name=metric_name,
                metric_value=float(value),
                sample_size=sample_size,
                extras=extras or {},
            )
            s.add(orm)

    # ----- Feedback -----
    def record_feedback(
        self,
        feedback_id: str,
        tweet_id: str,
        signal: str,
        category: Optional[str] = None,
        notes: Optional[str] = None,
        user_id: Optional[str] = None,
        snapshot: Optional[dict] = None,
    ) -> None:
        # snapshot may contain datetime objects — coerce to ISO strings
        snap = _json_safe(snapshot or {})
        with self.session() as s:
            orm = FeedbackORM(
                id=feedback_id,
                tweet_id=tweet_id,
                signal=signal,
                category=category,
                notes=notes,
                user_id=user_id,
                snapshot=snap,
            )
            s.add(orm)

    def feedback_for_tweet(self, tweet_id: str) -> list[dict]:
        """Return all feedback signals for a tweet (most recent first)."""
        out: list[dict] = []
        with self.session() as s:
            from sqlalchemy import select
            stmt = (
                select(FeedbackORM)
                .where(FeedbackORM.tweet_id == tweet_id)
                .order_by(FeedbackORM.created_at.desc())
            )
            for f in s.execute(stmt).scalars():
                out.append({
                    "id": f.id,
                    "signal": f.signal,
                    "category": f.category,
                    "notes": f.notes,
                    "created_at": f.created_at.isoformat() if f.created_at else None,
                })
        return out

    def feedback_aggregates(self, tweet_ids: Optional[list[str]] = None) -> dict:
        """Return {tweet_id: {up: n, down: n}} for the given ids (or all)."""
        out: dict[str, dict] = {}
        with self.session() as s:
            from sqlalchemy import select, func
            stmt = select(
                FeedbackORM.tweet_id,
                FeedbackORM.signal,
                func.count(FeedbackORM.id),
            ).group_by(FeedbackORM.tweet_id, FeedbackORM.signal)
            if tweet_ids:
                stmt = stmt.where(FeedbackORM.tweet_id.in_(tweet_ids))
            for tid, sig, n in s.execute(stmt):
                bucket = out.setdefault(tid, {"up": 0, "down": 0})
                if sig in bucket:
                    bucket[sig] = int(n)
        return out

    def feedback_summary(self) -> dict:
        """Top-level stats: total, by-signal, recent signals."""
        with self.session() as s:
            from sqlalchemy import select, func
            total = s.execute(select(func.count(FeedbackORM.id))).scalar() or 0
            up = (
                s.execute(
                    select(func.count(FeedbackORM.id)).where(FeedbackORM.signal == "up")
                ).scalar()
                or 0
            )
            down = (
                s.execute(
                    select(func.count(FeedbackORM.id)).where(FeedbackORM.signal == "down")
                ).scalar()
                or 0
            )
            recent = (
                s.execute(
                    select(FeedbackORM)
                    .order_by(FeedbackORM.created_at.desc())
                    .limit(20)
                ).scalars()
            )
            recent_list = [
                {
                    "id": f.id,
                    "tweet_id": f.tweet_id,
                    "signal": f.signal,
                    "category": f.category,
                    "notes": f.notes,
                    "created_at": f.created_at.isoformat() if f.created_at else None,
                }
                for f in recent
            ]
        return {"total": total, "up": up, "down": down, "recent": recent_list}

    def recent_metrics(self, model_name: str, n: int = 30) -> list[dict]:
        with self.session() as s:
            stmt = (
                select(ModelMetricORM)
                .where(ModelMetricORM.model_name == model_name)
                .order_by(ModelMetricORM.recorded_at.desc())
                .limit(n)
            )
            return [
                {
                    "metric_name": m.metric_name,
                    "metric_value": m.metric_value,
                    "version": m.version,
                    "sample_size": m.sample_size,
                    "recorded_at": m.recorded_at.isoformat() if m.recorded_at else None,
                }
                for m in s.execute(stmt).scalars()
            ]


_db_instance: Optional[Database] = None


def get_database() -> Database:
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
        _db_instance.init()
    return _db_instance