"""Runtime configuration. Every secret comes from the environment; nothing is hard-coded."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # --- Core infrastructure -------------------------------------------------
    database_url: str = "postgresql+psycopg://clipx:clipx@localhost:5432/clipx"
    redis_url: str = "redis://localhost:6379/0"
    public_app_url: str = "http://localhost:3000"  # the Cloudflare-hosted dashboard origin
    api_public_url: str = "http://localhost:8000"  # the FastAPI origin (behind Cloudflare Tunnel in prod)

    # --- Security --------------------------------------------------------------
    # Comma separated base64 32-byte keys. First key encrypts; all keys decrypt (rotation).
    token_encryption_keys: str = ""
    # Shared HMAC secret between the Cloudflare Worker (web) and this API.
    internal_api_secret: str = ""
    internal_signature_max_skew_s: int = 300
    # Optional: verify Cloudflare Access JWTs directly when the API itself sits behind Access.
    cf_access_team_domain: str = ""  # e.g. https://myteam.cloudflareaccess.com
    cf_access_aud: str = ""
    # Bootstrap allowlist. Additional emails are managed in the authorized_emails table.
    authorized_emails: str = ""
    rate_limit_per_minute: int = 240
    # Per-visit dashboard password (scrypt hash from `python -m app.security.password`). Required in production.
    dashboard_password_hash: str = ""
    unlock_ttl_minutes: int = 480
    unlock_max_attempts: int = 5

    # --- Twitch ---------------------------------------------------------------
    twitch_client_id: str = ""
    twitch_client_secret: str = ""
    twitch_target_login: str = "lacy"
    twitch_eventsub_transport: Literal["websocket", "webhook"] = "websocket"
    twitch_eventsub_secret: str = ""  # webhook transport only (10-100 ASCII chars)
    twitch_webhook_callback_url: str = ""  # must be https on port 443
    twitch_poll_interval_s: int = 60
    twitch_mirror_highlights_as_clips: bool = True

    # --- TikTok ---------------------------------------------------------------
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_scopes: str = "user.info.basic,video.publish,video.upload,video.list"
    # A URL prefix verified in the TikTok developer portal (required for PULL_FROM_URL).
    tiktok_pull_url_prefix: str = ""

    # --- Storage --------------------------------------------------------------
    storage_backend: Literal["r2", "local"] = "local"
    local_storage_dir: Path = Path("./var/storage")
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "clipx-media"
    r2_public_base_url: str = ""  # custom domain bound to the bucket (used for TikTok PULL_FROM_URL)

    # --- AI -------------------------------------------------------------------
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5-5"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    whisper_model: str = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"
    whisper_language: str | None = "en"

    # --- Live ingest / processing ---------------------------------------------
    # An authorized RTMP/SRT feed, e.g. rtmp://mediamtx:1935/live/<key>. Empty = no video signal.
    live_ingest_url: str = ""
    buffer_seconds: int = 240
    segment_seconds: int = 2
    analysis_window_seconds: int = 20
    work_dir: Path = Path("./var/work")
    render_preset: str = "veryfast"
    render_crf: int = 19

    youtube_api_key: str = ""

    @field_validator("whisper_language", mode="before")
    @classmethod
    def _empty_language(cls, v: str | None) -> str | None:
        return v or None

    @property
    def authorized_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.authorized_emails.split(",") if e.strip()}

    @property
    def twitch_configured(self) -> bool:
        return bool(self.twitch_client_id and self.twitch_client_secret)

    @property
    def tiktok_configured(self) -> bool:
        return bool(self.tiktok_client_key and self.tiktok_client_secret)

    @property
    def llm_configured(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def twitch_redirect_uri(self) -> str:
        return f"{self.public_app_url.rstrip('/')}/api/oauth/twitch/callback"

    @property
    def tiktok_redirect_uri(self) -> str:
        # TikTok requires static, absolute https URIs registered in the developer portal.
        return f"{self.public_app_url.rstrip('/')}/api/oauth/tiktok/callback/"

    def production_problems(self) -> list[str]:
        """Configuration errors that must block startup in production (fail closed)."""
        problems = []
        if not self.internal_api_secret or len(self.internal_api_secret) < 32:
            problems.append("INTERNAL_API_SECRET must be set (>= 32 chars)")
        if not self.token_encryption_keys:
            problems.append("TOKEN_ENCRYPTION_KEYS must be set")
        if not self.authorized_email_set:
            problems.append("AUTHORIZED_EMAILS must list at least one email")
        if not self.dashboard_password_hash.startswith(("scrypt:", "scrypt$")):
            problems.append("DASHBOARD_PASSWORD_HASH must be set (python -m app.security.password)")
        if self.storage_backend != "r2":
            problems.append("STORAGE_BACKEND must be r2 in production")
        return problems


class WeightsConfig(BaseSettings):
    """Default highlight scoring weights (overridable at runtime via app_settings)."""

    storytelling_context: float = Field(0.30)
    visual_interest: float = Field(0.25)
    audio_speech: float = Field(0.15)
    audience_reaction: float = Field(0.15)
    engagement_potential: float = Field(0.15)


@lru_cache
def get_settings() -> Settings:
    return Settings()
