"""Runtime settings, loaded from environment (via pydantic-settings)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "development"
    log_level: str = "INFO"

    client_config_dir: Path = Path("config/clients")

    redis_url: str = "redis://localhost:6379/0"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-7"

    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""

    webhook_default_secret: str = "change-me"

    # Max CRM retries before dead-letter alert
    crm_max_retries: int = 5
    crm_backoff_seconds: list[int] = Field(default_factory=lambda: [60, 300, 1800, 7200, 28800])

    # Whether the email validator should do a live MX lookup. Set False in
    # offline test/CI environments.
    email_check_deliverability: bool = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
