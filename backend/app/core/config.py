from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(REPO_ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    app_name: str = "Ristorante AI API"
    app_env: str = "development"
    debug: bool = False
    api_prefix: str = "/api"
    database_url: str = f"sqlite:///{(REPO_ROOT / 'data' / 'ristorante_ai.db').as_posix()}"
    allowed_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    default_timezone: str = "Europe/Rome"
    default_phone_region: str = "IT"
    log_level: str = "INFO"

    session_cookie_name: str = "ristorante_session"
    session_cookie_secure: bool = False
    jwt_secret: str = "change-me-in-production-with-at-least-32-bytes"
    jwt_expires_minutes: int = 60 * 24 * 7
    pii_encryption_key: str | None = None
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.0

    tool_secret_header_name: str = "X-Ristorante-Tool-Secret"
    tool_secret: str = Field(
        default="local-tool-secret",
        validation_alias=AliasChoices("TOOL_SECRET", "ELEVENLABS_TOOL_SECRET"),
    )
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_realtime_model: str = Field(default="gpt-realtime-2", validation_alias="OPENAI_REALTIME_MODEL")
    openai_realtime_voice: str = Field(default="cedar", validation_alias="OPENAI_REALTIME_VOICE")
    openai_realtime_base_url: str = Field(
        default="wss://api.openai.com/v1/realtime?model=gpt-realtime-2",
        validation_alias="OPENAI_REALTIME_BASE_URL",
    )
    public_base_url: str | None = Field(default=None, validation_alias="PUBLIC_BASE_URL")
    twilio_account_sid: str | None = Field(default=None, validation_alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str | None = Field(default=None, validation_alias="TWILIO_AUTH_TOKEN")
    public_web_base_url: str | None = Field(default=None, validation_alias="PUBLIC_WEB_BASE_URL")
    notification_from_email: str | None = Field(default=None, validation_alias="NOTIFICATION_FROM_EMAIL")
    smtp_host: str | None = Field(default=None, validation_alias="SMTP_HOST")
    smtp_port: int = Field(default=587, validation_alias="SMTP_PORT")
    smtp_username: str | None = Field(default=None, validation_alias="SMTP_USERNAME")
    smtp_password: str | None = Field(default=None, validation_alias="SMTP_PASSWORD")
    smtp_use_tls: bool = Field(default=True, validation_alias="SMTP_USE_TLS")
    smtp_use_ssl: bool = Field(default=False, validation_alias="SMTP_USE_SSL")

    auto_create_schema: bool = True
    seed_demo: bool | None = None
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = 60
    rate_limit_requests_per_window: int = 240
    rate_limit_auth_requests_per_window: int = 20
    rate_limit_sensitive_requests_per_window: int = 60
    rate_limit_public_requests_per_window: int = 120
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout_seconds: int = 5
    db_pool_recycle_seconds: int = 1800

    reservation_lab_booking_enabled: bool = Field(default=True, validation_alias="RESERVATION_LAB_BOOKING_ENABLED")
    default_restaurant_id: str = Field(default="r1", validation_alias="DEFAULT_RESTAURANT_ID")
    voice_audio_retention_days: int = Field(default=7, validation_alias="VOICE_AUDIO_RETENTION_DAYS")

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def split_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                return json.loads(stripped)
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return []

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if not isinstance(value, str):
            return value

        normalized = value.strip()
        if normalized.startswith("postgres://"):
            normalized = normalized.replace("postgres://", "postgresql+psycopg://", 1)
        elif normalized.startswith("postgresql://") and "+psycopg" not in normalized:
            normalized = normalized.replace("postgresql://", "postgresql+psycopg://", 1)

        if "supabase.co" not in normalized:
            return normalized

        parts = urlsplit(normalized)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.setdefault("sslmode", "require")
        return urlunsplit(parts._replace(query=urlencode(query)))

    @model_validator(mode="after")
    def resolve_seed_demo_default(self) -> Settings:
        if self.seed_demo is None:
            self.seed_demo = self.app_env != "production" and self.auto_create_schema
        return self

    @model_validator(mode="after")
    def warn_insecure_defaults(self) -> Settings:
        if self.app_env == "production":
            if self.jwt_secret == "change-me-in-production-with-at-least-32-bytes":
                raise ValueError(
                    "CRITICAL: JWT_SECRET is still the default placeholder. "
                    "Set a strong, unique secret via the JWT_SECRET environment variable before running in production."
                )
            if self.tool_secret == "local-tool-secret":
                raise ValueError(
                    "CRITICAL: TOOL_SECRET is still the default placeholder. "
                    "Set a strong secret via the TOOL_SECRET environment variable."
                )
            if not self.session_cookie_secure:
                raise ValueError(
                    "CRITICAL: SESSION_COOKIE_SECURE must be true in production so auth cookies are HTTPS-only."
                )
            if not self.pii_encryption_key:
                raise ValueError(
                    "CRITICAL: PII_ENCRYPTION_KEY must be set in production. "
                    "Do not fall back to JWT_SECRET for PII encryption."
                )
            if not self.openai_api_key:
                raise ValueError(
                    "CRITICAL: OPENAI_API_KEY must be set in production."
                )
            if not self.allowed_origins:
                raise ValueError("CRITICAL: ALLOWED_ORIGINS must list the deployed dashboard origins.")
            if "*" in self.allowed_origins:
                raise ValueError("CRITICAL: ALLOWED_ORIGINS cannot contain '*' in production.")
            if not self.public_base_url:
                raise ValueError(
                    "CRITICAL: PUBLIC_BASE_URL must be set in production "
                    "so Twilio can open the media stream and callbacks on the correct public domain."
                )
        return self


settings = Settings()


def escape_ini_interpolation(value: str) -> str:
    return value.replace("%", "%%")
