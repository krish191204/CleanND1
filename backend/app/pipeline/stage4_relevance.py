"""Stage 4: Relevance & quality filtering.

Goals:
- Decide if the tweet is *newsworthy* vs off-topic chatter.
- Detect burst events (many similar tweets in a short window).
- Compute a quality score (length, media, engagement, source diversity).

Uses sentence-transformers embeddings + a centroid-based relevance scorer
trained on a small set of seed news topics. If no model is trained, falls
back to engagement + length heuristics so the pipeline still produces
useful scores out of the box.

Issue 6 — known-handle tweets get a configurable burst credit so a single
tweet from @OpenAI can count as if it had 2 corroborating tweets. Combined
with the time-window count, this means a known-handle tweet still needs
≥ 1 real corroborator to fully burst, but doesn't need ≥ 3.

Issue 4 follow-on — consumes corroboration_group_id (set by Stage 2) so
two near-duplicate tweets from different known handles that survived
Stage 2's keep-both path can each contribute to the burst count without
needing to fall in the same time window.
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
        # Issue 6: known-handle burst credit. A single tweet from a known
        # handle counts as if it had this many corroborating tweets. Default
        # 2 means a known-handle tweet still needs >= 1 real corroborator
        # to burst (burst_min_count=4 minus 2 credit = 2 needed), but a
        # single known tweet can push a 3-tweet cluster to burst.
        known_handle_burst_credit: int = 2,
    ) -> None:
        super().__init__()
        self.relevance_threshold = relevance_threshold
        self.burst_window = burst_window_seconds
        self.burst_min_count = burst_min_count
        self.burst_jaccard = burst_jaccard
        self.known_handle_burst_credit = known_handle_burst_credit
        self._recent: dict[int, deque] = defaultdict(lambda: deque(maxlen=500))
        # Cross-cycle corroboration bucket — keyed by hash(corroboration_group_id).
        # No time-window purge here: Issue 4 follow-on says corroborating
        # tweets from different known handles can be in different cycles.
        # Capped at 1000 entries to bound memory.
        self._corr_counts: dict[int, int] = defaultdict(int)

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
        """Burst = many similar tweets in last `burst_window` seconds.

        Combines three signals:
          1. Time-window count: tweets with similar tokens in the last N sec
          2. Cross-cycle corroboration: any prior tweet with the same
             corroboration_group_id (set by Stage 2 for known-handle twins)
          3. Known-handle credit: configurable bump if the author is a
             curated known handle (Issue 6)
        """
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
        windowed_count = len(dq)

        # Issue 4 follow-on: corroboration_group_id accumulates across
        # cycles (no time-window purge). Each tweet with the same
        # corroboration_group_id contributes 1 to this bucket. Capped at
        # 1000 to bound memory.
        corr_count = 0
        if ct.corroboration_group_id:
            corr_key = hash("corr:" + ct.corroboration_group_id) & 0xFFFFFFFF
            self._corr_counts[corr_key] += 1
            corr_count = self._corr_counts[corr_key]
            if len(self._corr_counts) > 1000:
                # crude eviction: drop the smallest half
                sorted_keys = sorted(self._corr_counts.items(), key=lambda kv: kv[1])
                for k, _ in sorted_keys[: len(sorted_keys) // 2]:
                    del self._corr_counts[k]

        # Issue 6: known-handle credit. A single tweet from a known handle
        # counts as if it had `known_handle_burst_credit` corroborating
        # tweets. Default 2, so a single known-handle tweet + 2 real
        # corroborators = burst (4 ≥ 4).
        known_credit = 0
        if self.known_handle_burst_credit > 0:
            from ..services.known_handles import is_known_any
            if is_known_any(ct.raw.author_handle):
                known_credit = self.known_handle_burst_credit

        effective_count = windowed_count + corr_count + known_credit
        return effective_count >= self.burst_min_count

    # ---- public helpers for training/centering ----
    def fit_news_centroid(self, news_texts: list[str]) -> None:
        model = _Embedder.get()
        if not model:
            logger.warning("cannot fit centroid without embedder")
            return
        arr = model.encode(news_texts, normalize_embeddings=True, show_progress_bar=False)
        self._news_centroid = arr.mean(axis=0)
        self._news_centroid /= np.linalg.norm(self._news_centroid) + 1e-9