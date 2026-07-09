"""Centralized configuration loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Project root: backend/app/config.py -> backend/app -> backend -> root
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Application settings. Override via .env or environment variables."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ----- Twitter / X API -----
    twitter_api_base: str = "https://api.twitterapi.io"
    twitter_api_key: str = ""
    twitter_api_key_header: str = "X-API-Key"

    # ----- Database -----
    database_url: str = "sqlite:///./data/cleannd.db"

    # ----- Redis -----
    redis_url: str = "redis://localhost:6379/0"

    # ----- Backend -----
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    secret_key: str = "change_me"

    # ----- ML -----
    bot_model_path: str = "./ml/artifacts/bot_classifier.joblib"
    credibility_model_path: str = "./ml/artifacts/credibility.joblib"
    sentence_transformer_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    active_learning_margin_threshold: float = 0.15
    review_queue_batch_size: int = 50

    # ----- Pipeline thresholds -----
    min_followers: int = 500
    default_lang: str = "en"
    max_hashtags: int = 5
    max_urls: int = 2
    credibility_high_threshold: float = 0.55
    credibility_medium_threshold: float = 0.35
    credibility_known_news_handles_path: str = "./data/known_news_handles.json"
    # Default surface level — "medium" populates the demo feed with both
    # HIGH-credibility and MEDIUM-credibility items so the dashboard isn't
    # empty between mock-ingest ticks. Set to "high" for the strictest feed.
    surface_min_credibility: str = "medium"
    noise_reject_threshold: float = 0.30

    # ----- Software focus stage (Stage 0) — AI/ML + programming + tech -----
    software_focus_enabled: bool = True
    software_min_followers: int = 100
    software_min_account_age_days: int = 30
    software_min_engagement: int = 5
    software_known_accounts_path: str = "./data/known_software_accounts.json"
    software_require_all_signals: bool = False
    software_check_retweets: bool = True
    software_check_engagement: bool = True
    software_check_scam: bool = True
    software_check_profile_metadata: bool = True

    # ----- Mock auto-seed (kiosk mode) -----
    # When enabled, a background task periodically checks the surfaced feed
    # size; if it's below `mock_auto_seed_min_feed_size`, it runs a small
    # mock ingest to top it up. Keeps the dashboard populated without manual
    # clicks during demos / dev. Set MOCK_AUTO_SEED_ENABLED=false in tests.
    mock_auto_seed_enabled: bool = True
    mock_auto_seed_min_feed_size: int = 3
    mock_auto_seed_check_interval_seconds: float = 60.0
    mock_auto_seed_batch_size: int = 15
    mock_auto_seed_initial_delay_seconds: float = 2.0  # let the API finish booting first

    # ----- Stage 2 text-clean (Issue 4: keep both near-duplicates from
    # DIFFERENT known handles; tag with corroboration_group_id so Stage 4
    # can count them as a single corroboration event).
    stage2_skip_dedup_for_known_handles: bool = True

    # ----- Stage 3.5 noise (Issue 3: known-news handles get soft penalty,
    # not hard reject — their "We're thrilled to announce..." tweets should
    # pass through to Stage 5 which applies a small credibility_penalty).
    noise_skip_known_handles: bool = True

    # ----- Stage 4 burst (Issue 6: a single tweet from a known handle
    # counts as if it had `known_handle_burst_credit` corroborating tweets
    # toward burst detection. Default 2 means a known-handle tweet still
    # needs ≥ 1 real corroborator to burst, but doesn't need ≥ 3).
    known_handle_burst_credit: int = 2

    # ----- Real-ingest per-beat budget (Issue 5: was a single
    # max_persist_per_cycle cap shared across beats, which could starve
    # later beats if an earlier one filled the budget. Now per-beat).
    real_ingest_max_persist_per_beat: int = 15
    # Back-compat alias for the old single-cycle cap. New code reads
    # `real_ingest_max_persist_per_beat`; this is kept only for any
    # external scripts that referenced the old name.
    real_ingest_max_persist_per_cycle: int = 30
    # Tweets from handles in known_news_handles.json bypass the per-beat
    # cap and are persisted unconditionally.
    real_ingest_cap_priority_for_known_handles: bool = True

    # ----- Parallel query for known-news handles (Issue 1: alongside each
    # beat, run a `from:OpenAI OR from:Anthropic OR ...` query with no
    # min_faves so 0-5-minute-old breaking-news tweets from trusted
    # sources still surface).
    real_ingest_parallel_known_handle_query: bool = True

    # ----- Layer B: product-pivot additions (topic clustering + opinion tweets)
    # Credible-individuals whitelist (Addition 2/3) — researchers / founders /
    # practitioners whose opinions are valuable even when not breaking news.
    known_credible_individuals_path: str = "./data/known_credible_individuals.json"
    bypass_stages_for_known_individuals: bool = True   # Stage 0/3/3.5 skip for these handles

    # Topic clustering (Addition 1) — runs after Stage 5, before persistence.
    clustering_enabled: bool = True
    clustering_distance_threshold: float = 0.65       # cosine distance cutoff; smaller = tighter. 0.65 is loose enough to group near-related items even in the diverse mock dataset; real-world data with similar topics benefits from 0.25-0.4.
    clustering_min_cluster_size: int = 2             # singletons stay as solo cards
    clustering_min_tweets_for_label: int = 2          # min tweets before generating a TF-IDF label

    # Reactive topic expansion (Addition 5) — when a cluster with >= 2
    # tweets forms, immediately fire a one-shot ingest with the cluster's
    # top terms to surface additional coverage in real time.
    reactive_topic_expansion_enabled: bool = True
    reactive_expansion_max_results: int = 50
    reactive_expansion_cooldown_seconds: int = 3600   # 1 hour per topic

    # ----- Real-ingest background poller -----
    # When enabled, a background task polls twitterapi.io with the curated
    # queries in `real_ingest_queries` and runs the results through the
    # pipeline. Replaces the manual "click +15 mock" / "POST /api/ingest"
    # flow with continuous real-news ingestion.
    #
    # CAVEAT: twitterapi.io charges per call. With 5 queries × every 10
    # minutes × 25 tweets per query, that's ~180 calls/hour. Disable in
    # production (REAL_INGEST_ENABLED=false) unless you have a paid plan.
    real_ingest_enabled: bool = True
    real_ingest_interval_seconds: float = 600.0       # 10 min — credit-conscious
    real_ingest_initial_delay_seconds: float = 5.0
    real_ingest_query_delay_seconds: float = 7.0       # twitterapi.io free-tier caps at 1 request / 5 sec; 7s leaves a safety margin
    real_ingest_queries: list[str] = [
        "ai_news", "china_ai", "tech",
    ]
    real_ingest_max_per_query: int = 25
    real_ingest_max_persist_per_cycle: int = 30      # flood guard — lowered because free-tier QPS limits cap how many calls per cycle succeed
    # On TwitterAPIError or credits-exhausted 402, skip the rest of this
    # cycle and try again after one full interval (don't retry-storm).
    real_ingest_error_backoff: bool = True

    # ----- Runtime -----
    environment: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    enable_telemetry: bool = False

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def data_dir(self) -> Path:
        d = PROJECT_ROOT / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor (overridable via FastAPI dependency)."""
    return Settings()