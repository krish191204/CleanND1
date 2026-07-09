"""Wire the topic_grouper + tweet_type classifier into the persistence flow.

Called by `_ingest_real_to_db` and `_run_mock_ingest` after the pipeline
produces scored tweets but before they hit `database.upsert_tweet`. Runs:

  1. classify_tweet_type(scored)               # Addition 4
  2. cluster_tweets(scored)                     # Addition 1
  3. upsert each non-singleton cluster into topics
  4. set each tweet's topic_id via link_tweet_to_topic
  5. stamp tweet_type onto each tweet via set_tweet_type

The reactive-expansion fire-and-forget (Addition 5) is also scheduled from
here, gated by clustering_enabled + reactive_topic_expansion_enabled and
the per-topic cooldown.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from ..config import get_settings
from ..models.schemas import ScoredTweet
from ..pipeline.topic_grouper import Cluster, cluster_tweets
from ..pipeline.tweet_type import classify_tweet_type

from ..services.db import Database


def cluster_and_persist(
    scored: list[ScoredTweet],
    database: Database,
) -> list[Cluster]:
    """Run clustering + tweet_type classification on a batch of scored
    tweets, persist TopicORM rows + topic_id FKs. Returns the list of
    Cluster objects (singletons included) so the caller can drive
    reactive-expansion off the larger clusters.

    `scored` is a list of `ScoredTweet` instances returned by
    Pipeline.run(). The function mutates each scored tweet's
    `tweet_type` (set by classify_tweet_type) and assigns topic_id
    in-place via `database.link_tweet_to_topic` (persisted).

    If `clustering_enabled` is False in settings, this is a no-op except
    for the tweet_type classification (which is cheap).
    """
    s = get_settings()

    # 1. Tweet-type classification (always run — it's cheap and useful
    # for the dashboard's badge + filtering UX even without clustering).
    for st in scored:
        classify_tweet_type(st)

    # 2. Cluster (when enabled).
    if s.clustering_enabled and len(scored) > 0:
        clusters = cluster_tweets(
            scored,
            distance_threshold=s.clustering_distance_threshold,
            min_cluster_size=s.clustering_min_cluster_size,
        )
    else:
        # Clustering disabled — every tweet is a singleton.
        clusters = [
            Cluster(id=t.raw.id, tweets=[t], label="", anchor=t)
            for t in scored
        ]

    # 3. Persist TopicORM rows + tweet FKs. Singletons skip persistence.
    for c in clusters:
        if len(c.tweets) < 2:
            # Singleton: skip Topic row entirely; set topic_id=None on the
            # tweet (no-op if it was already None) and stamp tweet_type.
            for st in c.tweets:
                database.set_tweet_type(st.raw.id, st.tweet_type.value if hasattr(st.tweet_type, "value") else str(st.tweet_type))
                database.link_tweet_to_topic(st.raw.id, None)
            continue

        # Multi-tweet cluster: upsert Topic row.
        topic_id = c.id
        database.upsert_topic({
            "id": topic_id,
            "label": c.label,
            "anchor_tweet_id": c.anchor_tweet_id,
            "first_seen_at": min(t.processed_at for t in c.tweets).isoformat(),
            "last_activity_at": max(t.processed_at for t in c.tweets).isoformat(),
            "tweet_count": c.tweet_count,
            "extras": {},
        })
        # Set topic_id + tweet_type on each member tweet.
        for st in c.tweets:
            database.link_tweet_to_topic(st.raw.id, topic_id)
            database.set_tweet_type(
                st.raw.id,
                st.tweet_type.value if hasattr(st.tweet_type, "value") else str(st.tweet_type),
            )

    return clusters


def maybe_fire_reactive_expansion(clusters: list[Cluster]) -> None:
    """If reactive expansion is enabled, spawn an asyncio task for each
    cluster of size >= 2 whose last_expansion_at is older than the
    configured cooldown.

    Fire-and-forget — the expansion runs in the background and doesn't
    block the ingestion path.
    """
    s = get_settings()
    if not s.reactive_topic_expansion_enabled:
        return

    now = datetime.now(timezone.utc)
    cooldown_seconds = s.reactive_expansion_cooldown_seconds

    for c in clusters:
        if len(c.tweets) < 2:
            continue
        # Check cooldown against the persisted last_expansion_at. We
        # have to read from the DB for that; a small skip-on-error path
        # keeps this fast and best-effort.
        try:
            from ..services.db import Database as _DB
            database = _DB()
            topic = database.get_topic(c.id) or {}
            last = topic.get("last_expansion_at")
            if last:
                last_dt = datetime.fromisoformat(last)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                elapsed = (now - last_dt).total_seconds()
                if elapsed < cooldown_seconds:
                    continue
        except Exception:
            pass  # Cooldown check is best-effort.

        # Build a query from the cluster's top terms.
        terms = [t for t in c.label.split(" · ") if t.strip()]
        if not terms:
            terms = [c.anchor.raw.text.split()[0]] if c.anchor and c.anchor.raw.text else []
        if not terms:
            continue
        query = " OR ".join(f'"{t}"' for t in terms[:3]) + " lang:en"

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            # No loop in this thread (e.g. called from sync code). The
            # caller is responsible for running the expansions; we just
            # log.
            logger.debug(
                f"[real-ingest] no event loop; skipping reactive expansion for topic={c.id}"
            )
            return

        loop.create_task(_run_reactive_expansion(c.id, query, s))
        # Stamp the topic so we don't fire again within the cooldown.
        try:
            from ..services.db import Database as _DB
            _DB().record_topic_expansion(c.id)
        except Exception:
            pass


async def _run_reactive_expansion(topic_id: str, query: str, s) -> None:
    """Run a one-shot ingest for a single topic's top terms. Catches all
    exceptions so a failure here doesn't break anything else."""
    try:
        from ..api.routes import _ingest_real_to_db
        n = await _ingest_real_to_db(
            query_or_beat=query,
            max_results=s.reactive_expansion_max_results,
        )
        logger.info(
            f"[real-ingest] reactive expansion for topic={topic_id[:8]}… "
            f"persisted={n} (query: {query[:60]}…)"
        )
    except Exception as e:
        logger.warning(f"[real-ingest] reactive expansion failed for topic={topic_id[:8]}…: {e}")
