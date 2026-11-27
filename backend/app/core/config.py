"""Application configuration loaded strictly from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the Instagram AI Planner backend."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "staging", "production"] = "development"
    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"
    cors_origins: str = "http://localhost:3000"

    database_url: str = "sqlite+aiosqlite:///./social_media_ai.db"

    token_encryption_key: str = ""
    app_secret_key: str = "dev-only-secret-change-in-production"
    cron_secret: str = "dev-cron-secret-change-in-production"

    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_redirect_uri: str = "http://localhost:8000/api/auth/instagram/callback"
    meta_graph_version: str = "v21.0"
    meta_login_config_id: str = ""
    meta_oauth_scopes: str = (
        "instagram_basic,instagram_manage_insights,"
        "instagram_content_publish,pages_show_list,pages_read_engagement,business_management"
    )

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_fallback_models: str = "gemini-3-flash-preview,gemini-3.1-flash-lite,gemini-3.8-flash,gemini-3.6-flash,gemini-3.7-flash"
    gemini_enable_search_grounding: bool = True

    media_storage_provider: Literal["cloudinary", "s3", "local"] = "local"
    local_upload_dir: str = str(Path(__file__).resolve().parents[2] / "uploads")
    cloudinary_url: str = ""
    s3_bucket: str = ""
    s3_region: str = "auto"
    s3_endpoint_url: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_public_base_url: str = ""

    # --- Scheduler, Token & Media Spec Limits ---
    enable_internal_scheduler: bool = True
    worker_poll_interval_seconds: int = Field(default=30, ge=5, le=3600)
    worker_batch_limit: int = Field(default=10, ge=1, le=100)
    stale_publish_lock_minutes: int = Field(default=10, ge=1, le=120)
    insights_sync_interval_hours: int = Field(default=6, ge=1, le=24)
    token_refresh_threshold_days: int = Field(default=10, ge=1, le=45)
    max_publish_retries: int = Field(default=3, ge=0, le=10)

    # --- Hashtag Engine Hyperparameters ---
    hashtag_weekly_quota: int = Field(default=30, ge=1, le=100)
    hashtag_max_live_lookups: int = Field(default=4, ge=0, le=30)
    hashtag_cache_ttl_days: int = Field(default=7, ge=1, le=30)
    hashtag_bayesian_m: float = Field(default=2.0, ge=0.1, le=20.0)

    # --- Instagram Media Compliance Limits ---
    media_max_image_bytes: int = Field(default=8 * 1024 * 1024, ge=1024)
    media_max_video_bytes: int = Field(default=300 * 1024 * 1024, ge=1024)
    media_min_reel_seconds: float = Field(default=3.0, ge=1.0)
    media_max_reel_seconds: float = Field(default=900.0, ge=10.0)

    # --- 7-Stage Recommendation Engine Hyperparameters ---
    rec_likes_weight: float = 1.0
    rec_comments_weight: float = 2.5
    rec_shares_weight: float = 4.0
    rec_saves_weight: float = 4.0
    rec_clicks_weight: float = 2.0
    rec_half_life_days: float = Field(default=30.0, ge=1.0, le=365.0)
    rec_bayesian_m: float = Field(default=3.0, ge=0.1, le=50.0)
    rec_lookback_days: int = Field(default=90, ge=7, le=365)
    rec_platform_min_gap_hours: int = Field(default=12, ge=1, le=72)
    rec_min_lead_minutes: int = Field(default=30, ge=5, le=1440)
    rec_ucb_learning_c: float = 0.15
    rec_ucb_calibrating_c: float = 0.08
    rec_ucb_confident_c: float = 0.04

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        """Normalize postgres URLs (e.g., Neon/Supabase) for SQLAlchemy asyncpg."""
        if not isinstance(value, str):
            return value
        raw = value.strip()
        if raw.startswith("postgres://"):
            raw = "postgresql+asyncpg://" + raw[len("postgres://") :]
        elif raw.startswith("postgresql://"):
            raw = "postgresql+asyncpg://" + raw[len("postgresql://") :]

        if raw.startswith("postgresql+asyncpg://"):
            parts = urlsplit(raw)
            query_items = []
            for key, val in parse_qsl(parts.query, keep_blank_values=True):
                if key == "sslmode":
                    # asyncpg expects `ssl=require` instead of libpq's `sslmode`
                    query_items.append(("ssl", val))
                elif key != "channel_binding":
                    query_items.append((key, val))
            raw = urlunsplit(
                (parts.scheme, parts.netloc, parts.path, urlencode(query_items), parts.fragment)
            )
        return raw

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [item.strip().rstrip("/") for item in self.cors_origins.split(",") if item.strip()]
        frontend = self.frontend_url.strip().rstrip("/")
        if frontend and frontend not in origins:
            origins.append(frontend)
        return origins

    @property
    def meta_scope_list(self) -> list[str]:
        return [s.strip() for s in self.meta_oauth_scopes.split(",") if s.strip()]

    @property
    def gemini_fallback_model_list(self) -> list[str]:
        return [m.strip() for m in self.gemini_fallback_models.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
