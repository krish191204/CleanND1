"""Retraining pipeline: feed human-labeled reviews back into the bot classifier."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
from loguru import logger
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import f1_score, precision_score, recall_score
from sklearn.model_selection import train_test_split

from ..config import get_settings
from ..models.schemas import CleanedTweet, ReviewLabel
from ..services import get_database
from .features import extract_bot_features
from .train_bot import build_seed_dataset, train_bot_classifier


def _build_xy_from_reviews() -> tuple[list[list[float]], list[int], int]:
    """Convert reviewed items to (X, y, n_labeled)."""
    database = get_database()
    labeled = database.labeled_reviews_for_training()
    X: list[list[float]] = []
    y: list[int] = []
    for row in labeled:
        snap = row.get("snapshot") or {}
        raw = snap.get("raw", {}) or snap.get("clean", {}).get("raw", {}) or {}
        if not raw:
            continue
        # prefer the clean text from the snapshot when available
        clean = snap.get("clean") or {}
        clean_text = clean.get("clean_text") or raw.get("text", "")
        tokens = clean.get("tokens") or clean_text.split()
        # Reconstruct a CleanedTweet-shaped dict for the feature extractor
        ct_dict = {
            "raw": raw,
            "clean_text": clean_text,
            "tokens": tokens,
        }
        try:
            feats = extract_bot_features(ct_dict)
        except Exception as e:
            logger.warning(f"feature extract failed during retrain: {e}")
            continue

        # label mapping: approved = 0 (human), rejected = 1 (bot),
        # needs_more_info => skip
        lbl = row.get("label")
        if lbl == ReviewLabel.APPROVED.value:
            y.append(0)
        elif lbl == ReviewLabel.REJECTED.value:
            y.append(1)
        else:
            continue
        X.append(feats)
    return X, y, len(labeled)


def evaluate_models(X: list[list[float]], y: list[int]) -> dict:
    """Cross-validate the currently-saved classifier and return metrics."""
    s = get_settings()
    model_path = Path(s.bot_model_path)
    if not model_path.exists():
        return {"error": "no model yet"}
    clf = joblib.load(model_path)
    if len(set(y)) < 2 or len(y) < 5:
        return {"error": "insufficient labeled data", "n": len(y)}
    _, X_test, _, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    y_pred = clf.predict(X_test)
    return {
        "f1": float(f1_score(y_test, y_pred, zero_division=0)),
        "precision": float(precision_score(y_test, y_pred, zero_division=0)),
        "recall": float(recall_score(y_test, y_pred, zero_division=0)),
        "n": len(y),
    }


def retrain_bot_classifier(
    epochs: int = 1,
    augment_with_seed: bool = True,
) -> dict:
    """Pull reviewed items + 👍/👎 feedback, optionally augment with seed data, refit."""
    s = get_settings()
    X, y, n_labeled = _build_xy_from_reviews()
    logger.info(f"loaded {n_labeled} human-labeled reviews; usable={len(y)}")

    # Ingest direct 👍/👎 feedback and map: up -> 0 (human), down -> 1 (bot)
    feedback_added = _add_feedback_signals(X, y)
    logger.info(f"added {feedback_added} feedback rows")

    if augment_with_seed and len(y) < 200:
        seed_rows, y_seed = build_seed_dataset(n=400)
        X_seed = [extract_bot_features(r) for r in seed_rows]
        X = X + X_seed
        y = y + y_seed
        logger.info(f"augmented with {len(y_seed)} seed rows -> total={len(y)}")

    if len(y) < 20:
        return {"status": "skipped", "reason": "not enough labeled data", "n_labeled": n_labeled}

    out = train_bot_classifier(X=X, y=y, out_path=s.bot_model_path)

    # record metric
    database = get_database()
    database.record_metric(
        model_name="bot_classifier",
        version=datetime.utcnow().strftime("%Y%m%d-%H%M%S"),
        metric_name="f1",
        value=out["metrics"]["f1"],
        sample_size=len(y),
        extras={
            "precision": out["metrics"]["precision_bot"],
            "recall": out["metrics"]["recall_bot"],
            "n_review": n_labeled,
            "n_feedback": feedback_added,
        },
    )
    return {
        "status": "ok",
        "n_labeled": n_labeled,
        "n_feedback": feedback_added,
        **out,
    }


def _add_feedback_signals(X: list[list[float]], y: list[int]) -> int:
    """Convert 👍/👎 feedback signals into training rows.

    up   -> human (label 0)
    down -> bot/spam (label 1)
    """
    from ..models.db_models import FeedbackORM
    from sqlalchemy import select

    database = get_database()
    added = 0
    with database.session() as s:
        rows = s.execute(
            select(FeedbackORM).order_by(FeedbackORM.created_at.desc()).limit(500)
        ).scalars()

        for f in rows:
            snap = f.snapshot or {}
            raw = snap.get("author_id") and snap.get("text")
            # Robust extraction — prefer persisted snapshot, otherwise text from raw
            text = snap.get("text") or snap.get("clean_text") or ""
            if not text:
                # fallback: scan for an "raw" subdict
                inner_raw = snap.get("raw") or {}
                text = inner_raw.get("text", "")
            if not text:
                continue
            # need to reconstruct a CleanedTweet-shaped dict for the feature extractor
            token_list = text.lower().split()
            ct_dict = {
                "raw": snap.get("raw", {}),
                "clean_text": text.lower(),
                "tokens": token_list,
            }
            try:
                feats = extract_bot_features(ct_dict)
            except Exception as e:
                logger.warning(f"feedback feature extract failed: {e}")
                continue
            X.append(feats)
            y.append(0 if f.signal == "up" else 1)
            added += 1
    return added


if __name__ == "__main__":  # pragma: no cover
    print(retrain_bot_classifier())