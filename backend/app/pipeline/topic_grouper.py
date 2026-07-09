"""Topic clustering (Layer B Addition 1).

A lightweight grouper that takes the embeddings Stage 4 already produced
and runs AgglomerativeClustering with cosine distance to find topic
clusters. Each cluster becomes a TopicORM row; each member tweet gets a
topic_id FK.

We intentionally re-use the existing Stage 4 embeddings rather than
re-embedding — keeps the clustering fast (no model call) and consistent
with what the relevance burst detector uses.

Algorithm:
  1. Drop tweets without embeddings.
  2. Stack the embeddings, L2-normalise, build the cosine-distance matrix
     (1 - cos_sim).
  3. Run sklearn.cluster.AgglomerativeClustering with `metric='precomputed'`
     and `linkage='average'`. `distance_threshold` controls cluster
     tightness — smaller = tighter (0.25 default ≈ 0.75 cosine sim).
  4. Bucket tweets by cluster id.
  5. Singletons (no embedding) fall into their own unclustered group with
     label='' — these stay solo cards on the dashboard.
  6. For each cluster, sort by final_score desc; pick the anchor
     (highest-scoring). Generate a TF-IDF label from the cluster's tweet
     texts — top 3 terms joined by ' · '.

This is a pure-Python module — only depends on scikit-learn and numpy,
which are already in requirements.txt.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from loguru import logger

from ..models.schemas import ScoredTweet


@dataclass
class Cluster:
    """One cluster produced by `cluster_tweets`."""
    id: str                              # cluster local id, used to key TopicORM
    tweets: list[ScoredTweet] = field(default_factory=list)
    label: str = ""                      # TF-IDF top terms joined by ' · '
    anchor: Optional[ScoredTweet] = None  # highest-final_score member

    @property
    def anchor_tweet_id(self) -> Optional[str]:
        return self.anchor.raw.id if self.anchor else None

    @property
    def tweet_count(self) -> int:
        return len(self.tweets)


def cluster_tweets(
    scored: list[ScoredTweet],
    *,
    distance_threshold: float = 0.25,
    min_cluster_size: int = 2,
    min_tweets_for_label: int = 3,
) -> list[Cluster]:
    """Run Agglomerative clustering on the scored tweets. Returns a list of
    Cluster objects (one per cluster; singletons included with label='').

    Singletons (tweets whose cluster ended up alone) are kept here so the
    caller knows not to drop them. The caller can decide whether to
    persist them as a Topic row (recommended: skip — they're just
    ungrouped cards).
    """
    if not scored:
        return []

    # 1. Drop tweets without embeddings.
    usable: list[ScoredTweet] = [s for s in scored if s.embedding]
    if len(usable) < min_cluster_size:
        # Not enough embeddings to form a cluster. Every tweet is its own
        # singleton — caller can choose to persist or not.
        return [
            Cluster(id=_cluster_id([s]), tweets=[s], label="", anchor=s)
            for s in scored
        ]

    # 2. Stack embeddings + cosine-distance matrix.
    X = np.array([s.embedding for s in usable], dtype=np.float32)
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    Xn = X / norms
    sim = Xn @ Xn.T
    np.clip(sim, -1.0, 1.0, out=sim)
    dist = 1.0 - sim
    np.fill_diagonal(dist, 0.0)

    # 3. Agglomerative clustering.
    from sklearn.cluster import AgglomerativeClustering
    agg = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=distance_threshold,
        metric="precomputed",
        linkage="average",
    )
    labels = agg.fit_predict(dist)

    # 4. Bucket tweets by cluster id (in-tweet order).
    buckets: dict[int, list[ScoredTweet]] = {}
    for s, lab in zip(usable, labels):
        buckets.setdefault(int(lab), []).append(s)

    # 5. Singletons (no embedding) fall into their own group with id > max.
    solo_unused = [s for s in scored if not s.embedding]
    next_id = (max(buckets.keys()) + 1) if buckets else 0
    for s in solo_unused:
        buckets[next_id] = [s]
        next_id += 1

    # 6. Build Cluster objects with anchor + label.
    clusters: list[Cluster] = []
    sorted_bucket_ids = sorted(buckets.keys())
    for cid in sorted_bucket_ids:
        members = buckets[cid]
        members_sorted = sorted(members, key=lambda s: s.final_score, reverse=True)
        anchor = members_sorted[0]
        label = (
            _label_for(members_sorted, min_tweets_for_label)
            if len(members_sorted) >= min_tweets_for_label
            else ""
        )
        clusters.append(
            Cluster(
                id=_cluster_id(members_sorted),
                tweets=members_sorted,
                label=label,
                anchor=anchor,
            )
        )
    return clusters


def _label_for(tweets: list[ScoredTweet], min_tweets_for_label: int) -> str:
    """Top 3 TF-IDF terms from the cluster's tweet texts, joined by ' · '.
    Falls back to a high-frequency-noun heuristic if scikit-learn isn't
    available."""
    texts = [(t.raw.text or "") for t in tweets]
    if not texts:
        return ""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        vec = TfidfVectorizer(
            stop_words="english",
            max_features=10,
            ngram_range=(1, 2),
            token_pattern=r"(?u)\b[A-Za-z][A-Za-z]+\b",  # words only, drop numbers
        )
        # fit_transform expects >= 2 docs; if we only have 1, pad with empty
        corpus = texts if len(texts) >= 2 else texts + [""]
        try:
            tfidf = vec.fit_transform(corpus)
        except ValueError:
            # Empty vocabulary (e.g. only stop-words + emojis). Skip label.
            return ""
        # Mean TF-IDF per term across the cluster, pick the top 3.
        scores = np.asarray(tfidf.mean(axis=0)).ravel()
        terms = vec.get_feature_names_out()
        top_idx = scores.argsort()[::-1][:3]
        top_terms = [terms[i] for i in top_idx if scores[i] > 0]
        return " · ".join(top_terms)
    except Exception as e:  # pragma: no cover
        logger.warning(f"TF-IDF label generation failed: {e}")
        return ""


def _cluster_id(tweets: list[ScoredTweet]) -> str:
    """Stable id derived from member tweet ids, plus the longest text's
    fingerprint. Stable across runs so a cluster with the same members
    gets the same id (lets the DB upsert idempotently)."""
    import hashlib
    members = "|".join(sorted(t.raw.id for t in tweets))
    if tweets:
        longest = max((t.raw.text or "") for t in tweets)
        seed = f"{members}|{longest[:200]}"
    else:
        seed = members
    return hashlib.md5(seed.encode()).hexdigest()[:32]
