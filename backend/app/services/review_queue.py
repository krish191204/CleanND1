"""Review queue business-logic: scoring, prioritization, export-to-training."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from loguru import logger

from ..models.schemas import ReviewItem
from .db import Database


class ReviewQueue:
    """Persistence facade over the ReviewORM table."""

    def __init__(self, db: Optional[Database] = None) -> None:
        self.db = db or Database()

    def push(self, items: list[ReviewItem]) -> int:
        n = 0
        for it in items:
            try:
                self.db.queue_review({
                    "id": it.id,
                    "tweet_id": it.tweet_id,
                    "snapshot": it.snapshot,
                    "model_bot_score": it.model_bot_score,
                    "model_credibility": it.model_credibility,
                    "model_relevance": it.model_relevance,
                    "uncertainty_margin": it.uncertainty_margin,
                    "label": None,
                    "category": it.category,
                    "notes": it.notes,
                    "labeler_id": None,
                    "labeled_at": None,
                })
                n += 1
            except Exception as e:
                logger.warning(f"failed to queue review: {e}")
        return n

    def next_batch(self, n: int = 25) -> list[dict]:
        items = self.db.next_reviews_unlabeled(n)
        return [self._serialize(it) for it in items]

    def label(
        self,
        review_id: str,
        label: str,
        category: Optional[str] = None,
        notes: Optional[str] = None,
        labeler_id: Optional[str] = "anonymous",
    ) -> bool:
        self.db.label_review(review_id, label, category, notes, labeler_id)
        logger.info(f"review {review_id} -> {label} ({category})")
        return True

    def labeled_dataset(self) -> list[dict]:
        return self.db.labeled_reviews_for_training()

    def stats(self) -> dict:
        items = self.db.review_stats_aggregate()
        return items

    @staticmethod
    def _serialize(orm) -> dict:
        if isinstance(orm, dict):
            return orm
        return {
            "id": orm.id,
            "tweet_id": orm.tweet_id,
            "snapshot": orm.snapshot,
            "model_bot_score": orm.model_bot_score,
            "model_credibility": orm.model_credibility,
            "model_relevance": orm.model_relevance,
            "uncertainty_margin": orm.uncertainty_margin,
            "label": orm.label,
            "category": orm.category,
            "notes": orm.notes,
            "labeler_id": orm.labeler_id,
            "labeled_at": orm.labeled_at.isoformat() if orm.labeled_at else None,
            "created_at": orm.created_at.isoformat() if orm.created_at else None,
        }