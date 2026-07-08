"""Stage 4: Relevance & quality filtering.

Goals:
- Decide if the tweet is *newsworthy* vs off-topic chatter.
- Detect burst events (many similar tweets in a short window).
- Compute a quality score (length, media, engagement, source diversity).

Uses sentence-transformers embeddings + a centroid-based relevance scorer
trained on a small set of seed news topics. If no model is trained, falls
back to engagement + length heuristics so the pipeline still produces
useful scores out of the box.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Optional

import numpy as np
from loguru import logger

from ..config import get_settings
from ..models.schemas import CleanedTweet
from .base import Stage, StageResult


class _Embedder:
    """Lazy wrapper around sentence-transformers."""
    _model = None
    _name: Optional[str] = None

    @classmethod
    def get(cls, model_name: Optional[str] = None):
        if cls._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                cls._name = model_name or get_settings().sentence_transformer_model
                cls._model = SentenceTransformer(cls._name)
                logger.info(f"[embed] loaded {cls._name}")
            except Exception as e:  # pragma: no cover
                logger.warning(f"[embed] failed to load sentence-transformers: {e}")
                cls._model = False  # type: ignore
        return cls._model


class RelevanceFilter(Stage[CleanedTweet, CleanedTweet]):
    """Annotates each tweet with relevance_score, quality_score, burst flag."""

    name = "stage4_relevance"

    def __init__(
        self,
        relevance_threshold: float = 0.35,
        burst_window_seconds: int = 300,
        burst_min_count: int = 4,
        burst_jaccard: float = 0.5,
    ) -> None:
        super().__init__()
        self.relevance_threshold = relevance_threshold
        self.burst_window = burst_window_seconds
        self.burst_min_count = burst_min_count
        self.burst_jaccard = burst_jaccard
        self._recent: dict[int, deque] = defaultdict(lambda: deque(maxlen=500))

        # Optional: a precomputed centroid for "is this news?"
        self._news_centroid: Optional[np.ndarray] = None
        self._topic_centroids: dict[str, np.ndarray] = {}

        # Topic keywords (cheap fallback when no embedding model)
        self.topic_keywords: dict[str, list[str]] = {
            "world": ["war", "election", "president", "minister", "sanction", "border"],
            "tech": ["ai", "model", "openai", "google", "chip", "release", "launch"],
            "business": ["stock", "market", "earnings", "merger", "acquire", "ipo"],
            "science": ["study", "research", "discovery", "nasa", "telescope", "climate"],
        }

    # ------------------------------------------------------------------
    def process(self, items: list[CleanedTweet]) -> StageResult[CleanedTweet]:
        passed: list[CleanedTweet] = []
        rejected: list[tuple[CleanedTweet, str]] = []

        # Batch-embed for efficiency
        texts = [ct.clean_text for ct in items]
        embeddings = self._embed_batch(texts)

        for ct, emb in zip(items, embeddings):
            ct.embedding = emb.tolist() if emb is not None else None
            rel = self._relevance(ct, emb)
            qual = self._quality(ct)
            ct.relevance_score = rel
            ct.quality_score = qual

            # burst detection
            burst = self._is_burst(ct)
            ct.is_burst_event = burst
            if burst:
                ct.relevance_score = max(ct.relevance_score, 0.6)

            if rel < self.relevance_threshold:
                rejected.append((ct, f"relevance={rel:.2f}"))
            else:
                passed.append(ct)

        return StageResult(
            passed=passed,
            rejected=rejected,
            stats={"input": len(items), "passed": len(passed), "rejected": len(rejected)},
        )

    # ------------------------------------------------------------------
    def _embed_batch(self, texts: list[str]) -> list[Optional[np.ndarray]]:
        model = _Embedder.get()
        if not model:
            return [None] * len(texts)
        try:
            arr = model.encode(
                texts,
                batch_size=32,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return [a for a in arr]
        except Exception as e:  # pragma: no cover
            logger.warning(f"[embed] batch encode failed: {e}")
            return [None] * len(texts)

    def _relevance(self, ct: CleanedTweet, emb: Optional[np.ndarray]) -> float:
        # Embedding-based: cosine to a generic "news" centroid.
        if emb is not None and self._news_centroid is not None:
            sim = float(np.dot(emb, self._news_centroid))
            # squash [-1, 1] -> [0, 1]
            return float((sim + 1) / 2)

        # Fallback: keyword hit density
        toks = set(ct.lemmas)
        if not toks:
            return 0.0
        hits = 0
        for kws in self.topic_keywords.values():
            for k in kws:
                if k in toks:
                    hits += 1
        # 1 hit = 0.4, 3+ = 0.9
        score = min(1.0, 0.4 + 0.25 * hits)
        # length & media bump
        if ct.raw.media:
            score = min(1.0, score + 0.1)
        return score

    def _quality(self, ct: CleanedTweet) -> float:
        tw = ct.raw
        score = 0.0
        n = len(tw.text)
        if 60 <= n <= 280:
            score += 0.4
        elif 30 <= n <= 400:
            score += 0.2
        if tw.media:
            score += 0.2
        eng = tw.like_count + tw.retweet_count + tw.reply_count + tw.quote_count
        score += min(0.3, np.log1p(eng) / 10.0)
        if tw.author_verified:
            score += 0.1
        return min(score, 1.0)

    def _is_burst(self, ct: CleanedTweet) -> bool:
        """Burst = many similar tweets in last `burst_window` seconds."""
        if not ct.tokens:
            return False
        key = hash(tuple(sorted(ct.tokens[:8]))) & 0xFFFFFFFF
        now = time.time()
        dq = self._recent[key]
        dq.append(now)
        # purge old
        cutoff = now - self.burst_window
        while dq and dq[0] < cutoff:
            dq.popleft()
        return len(dq) >= self.burst_min_count

    # ---- public helpers for training/centering ----
    def fit_news_centroid(self, news_texts: list[str]) -> None:
        model = _Embedder.get()
        if not model:
            logger.warning("cannot fit centroid without embedder")
            return
        arr = model.encode(news_texts, normalize_embeddings=True, show_progress_bar=False)
        self._news_centroid = arr.mean(axis=0)
        self._news_centroid /= np.linalg.norm(self._news_centroid) + 1e-9