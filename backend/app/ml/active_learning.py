"""Active learning: uncertainty + diversity sampling to grow the review queue."""
from __future__ import annotations

import random
from typing import Iterable, List

import numpy as np

from ..models.schemas import ScoredTweet


def _uncertainty(t: ScoredTweet) -> float:
    bot_u = 1.0 - abs(t.clean.bot_score - 0.5) * 2
    cred_u = 1.0 - abs(t.credibility_score - 0.5) * 2
    rel_u = 1.0 - abs(t.clean.relevance_score - 0.5) * 2
    return 0.5 * bot_u + 0.3 * cred_u + 0.2 * rel_u


def select_for_labeling(
    tweets: Iterable[ScoredTweet],
    n: int = 50,
    margin_threshold: float = 0.15,
) -> List[ScoredTweet]:
    """Pick the top-n tweets where the model is most uncertain.

    Items with `uncertainty < (1 - margin_threshold)` are skipped.
    """
    scored = [
        (t, _uncertainty(t))
        for t in tweets
        if _uncertainty(t) >= (1.0 - margin_threshold)
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in scored[:n]]


def diversity_sample(
    tweets: list[ScoredTweet],
    embeddings: np.ndarray | None,
    n: int = 20,
    seed: int = 42,
) -> list[ScoredTweet]:
    """Pick a diverse subset (k-means++ style) for hard-negative mining.

    Falls back to random sampling if embeddings are unavailable.
    """
    if embeddings is None or len(tweets) <= n:
        return list(tweets)[:n]
    rng = np.random.default_rng(seed)
    chosen = [int(rng.integers(0, len(tweets)))]
    for _ in range(n - 1):
        sims = embeddings @ embeddings[chosen].T
        max_sim = sims.max(axis=1)
        # Exclude already chosen
        for c in chosen:
            max_sim[c] = -1.0
        # Pick the point with lowest max-similarity
        nxt = int(np.argmax(-max_sim))
        chosen.append(nxt)
    return [tweets[i] for i in chosen]