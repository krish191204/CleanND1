"""Celery tasks (used in production for periodic retraining).

In dev/MVP these run inline via `background.add_task` in the FastAPI app.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from celery import Celery
from loguru import logger

from ..config import get_settings
from .retrain import retrain_bot_classifier


settings = get_settings()

celery_app = Celery(
    "cleannd",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.beat_schedule = {
    "retrain-bot-nightly": {
        "task": "backend.app.ml.celery_app.retrain_bot_task",
        "schedule": 24 * 60 * 60,  # every 24h
        "args": (),
    },
}


@celery_app.task(name="backend.app.ml.celery_app.retrain_bot_task")
def retrain_bot_task() -> dict:
    logger.info("running nightly bot-classifier retrain")
    result = retrain_bot_classifier()
    logger.info(f"retrain result: {result}")
    return result


@celery_app.task(name="backend.app.ml.celery_app.retrain_trigger")
def retrain_trigger() -> dict:
    return retrain_bot_task.delay().id  # type: ignore[attr-defined]


def run_inline() -> dict:
    """Synchronous wrapper for `BackgroundTasks` in FastAPI."""
    return retrain_bot_classifier()