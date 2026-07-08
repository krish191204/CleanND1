"""Train the bot classifier on a labeled dataset (or seed rules)."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
from loguru import logger
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, f1_score, precision_score, recall_score
from sklearn.model_selection import train_test_split

from ..config import get_settings
from ..models.schemas import CleanedTweet, RawTweet
from .features import extract_bot_features


# --- seed dataset: heuristic-labeled tweets used to bootstrap the model.
#   In production, replace this with a labeled set of real tweets.
SEED_TEMPLATES_HUMAN = [
    ("just got back from a great hike in the mountains!", 0),
    ("anyone else think the new apple event was underwhelming?", 0),
    ("reading a fascinating book on renaissance art. highly recommend", 0),
    ("had dinner with @friend last night — amazing pasta place in soho", 0),
    ("looking for recommendations on good mechanical keyboards", 0),
    ("watched the match today. what a comeback in the second half!", 0),
    ("my thoughts on the latest research paper: link", 0),
    ("spent the weekend at the lake. here's a photo", 0),
    ("happy birthday to my sister 🎉", 0),
    ("writing up notes from today's conference. takeaways below", 0),
    # Software-sphere templates — match the synthetic /api/ingest/mock generator
    # in `app/api/routes.py:ingest_mock` so the classifier recognises the demo
    # data as human out-of-the-box.
    ("Anthropic released a new version of Claude — claiming better benchmark scores on coding and reasoning tasks. Paper linked in the release notes.", 0),
    ("We benchmarked llama vs claude vs gpt on our internal eval suite — results and methodology in the paper. Inference latency was the surprise.", 0),
    ("Next.js 16 just shipped with improved build performance. Opened a PR upstream to add migration notes from v15.", 0),
    ("Kubernetes 1.32 release notes are out — the new sidecar feature changes how we run our service mesh. Breaking change for our deployment.", 0),
    ("NeurIPS 2026 papers list is live — multiple papers on transformer attention and inference optimization this year.", 0),
    ("GitHub just made the API rate limit change and broke our build. Here's the patch I opened upstream and the migration in our repo.", 0),
    ("We migrated our inference pipeline from pytorch to vllm — latency dropped 3x, here's the new architecture and the benchmark numbers.", 0),
    ("Stripe shared a great engineering blog post about their API design choices and why they deprecated that endpoint. Worth a read.", 0),
    ("New release of pytorch with improved compilation — eager mode is finally competitive with the compiled path for our training workload.", 0),
    ("Just merged the migration to postgres 17 in our repo. The performance improvement on our analytics queries is real.", 0),
    ("TypeScript 5.7 release notes: the new type system improvements clean up a lot of legacy code in our api handlers.", 0),
    ("Docker build cache invalidation after a dependency update is the source of 80% of our CI pain. Filed an issue upstream.", 0),
]

SEED_TEMPLATES_BOT = [
    ("BUY NOW limited offer click here http://spam.example http://spam.example http://spam.example 🚀🚀🚀🚀🚀 #deal #sale #limited #win #free", 1),
    ("make $5000/day with this one weird trick click here http://spam.example", 1),
    ("dm me for crypto signals 🚀🚀🚀🚀 guaranteed returns #crypto #bitcoin", 1),
    ("looking for promo! link in bio onlyfans premium content", 1),
    ("FREE followers in 24 hours click here http://spam.example", 1),
    ("cheap nike shoes http://spam.example #shoes #sale #fashion", 1),
    ("make money from home with this AI tool 🚀🚀🚀🚀🚀", 1),
    ("click here for free iphone! http://spam.example", 1),
    ("hot singles in your area want to chat dm me", 1),
    ("earn passive income crypto signal group telegram me", 1),
]


def build_seed_dataset(n: int = 400) -> tuple[list[CleanedTweet], list[int]]:
    """Synthesize a labeled seed dataset of n tweets (50/50 split by default)."""
    rng = random.Random(42)
    rows: list[CleanedTweet] = []
    labels: list[int] = []

    handles_human = [
        "ada_codes", "kestrel_dev", "mira_open", "soren_mlops", "jie_l",
        "alice_dev", "maria_news", "tom_eco", "jenna_smith", "davidk", "kira_art",
    ]
    handles_bot = ["deal_hunter_24", "promo_king", "click4cash", "foll0wers_fast", "free_iphone_now"]

    for _ in range(n // 2):
        tmpl, lbl = rng.choice(SEED_TEMPLATES_HUMAN)
        handle = rng.choice(handles_human)
        rows.append(_make_cleaned(tmpl, handle, lbl))
        labels.append(lbl)
    for _ in range(n // 2):
        tmpl, lbl = rng.choice(SEED_TEMPLATES_BOT)
        handle = rng.choice(handles_bot)
        rows.append(_make_cleaned(tmpl, handle, lbl))
        labels.append(lbl)

    rng.shuffle(rows)
    rng.shuffle(labels)
    return rows, labels


def _make_cleaned(text: str, handle: str, label: int) -> CleanedTweet:
    is_bot = label == 1
    now = datetime.now(timezone.utc)
    author_created = now - timedelta(days=30 if is_bot else 365 * 4)
    raw = RawTweet(
        id=str(hash(text + handle) & 0xFFFFFFFFFFFFFFFF),
        text=text,
        author_id=str(hash(handle) & 0xFFFFFFFF),
        author_handle=handle,
        author_display_name=handle,
        author_followers=rng_below(150) if is_bot else rng_below(20_000),
        author_following=rng_below(5000) if is_bot else rng_below(2000),
        author_verified=not is_bot and rng_below_bool(0.3),
        author_created_at=author_created,
        author_profile_image_url=None if is_bot else f"https://x.com/{handle}.jpg",
        author_description="" if is_bot else "ml engineer • python • pytorch",
        lang="en",
        created_at=now,
        hashtags=[w for w in text.split() if w.startswith("#")],
        urls=[w for w in text.split() if w.startswith("http")],
        mentions=[w for w in text.split() if w.startswith("@")],
        media=[],
        like_count=rng_below(2) if is_bot else rng_below(500),
        retweet_count=rng_below(50) if not is_bot else 0,
        reply_count=rng_below(1) if is_bot else rng_below(20),
        quote_count=0,
    )
    return CleanedTweet(
        raw=raw,
        clean_text=text.lower(),
        tokens=text.lower().split(),
        lemmas=text.lower().split(),
        language="en",
    )


def rng_below(n: int) -> int:
    import random
    return random.randint(0, max(n - 1, 0))


def rng_below_bool(p: float) -> bool:
    import random
    return random.random() < p


def train_bot_classifier(
    X: list[list[float]] | None = None,
    y: list[int] | None = None,
    out_path: Optional[str] = None,
    random_state: int = 42,
) -> dict:
    """Train RandomForest on the given (or seed) dataset; save and return metrics."""
    s = get_settings()
    if X is None or y is None:
        logger.info("building seed dataset...")
        rows, y = build_seed_dataset()
        X = [extract_bot_features(r) for r in rows]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=random_state, stratify=y
    )

    clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=12,
        min_samples_leaf=3,
        class_weight="balanced",
        n_jobs=-1,
        random_state=random_state,
    )
    clf.fit(X_train, y_train)
    y_pred = clf.predict(X_test)

    metrics = {
        "f1": float(f1_score(y_test, y_pred, zero_division=0)),
        "precision_bot": float(precision_score(y_test, y_pred, pos_label=1, zero_division=0)),
        "recall_bot": float(recall_score(y_test, y_pred, pos_label=1, zero_division=0)),
        "precision_human": float(precision_score(y_test, y_pred, pos_label=0, zero_division=0)),
        "recall_human": float(recall_score(y_test, y_pred, pos_label=0, zero_division=0)),
        "n_train": len(y_train),
        "n_test": len(y_test),
    }
    logger.info("trained bot classifier:\n" + classification_report(y_test, y_pred, zero_division=0))

    out = Path(out_path or s.bot_model_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(clf, out)
    logger.info(f"saved bot classifier to {out}")

    return {"metrics": metrics, "model_path": str(out)}


if __name__ == "__main__":  # pragma: no cover
    out = train_bot_classifier()
    print(out)