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
    real_ingest_queries: list[str] = [
        "ai_news", "china_ai", "tech",
    ]
    real_ingest_max_per_query: int = 25
    real_ingest_max_persist_per_cycle: int = 50      # flood guard
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