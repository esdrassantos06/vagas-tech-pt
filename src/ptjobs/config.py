"""Application settings loaded from the environment."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables and .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://ptjobs:ptjobs@localhost:5432/ptjobs"
    )
    public_base_url: str = "http://localhost:8000"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "alerts@example.com"

    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    telegram_webhook_secret: str = ""

    admin_discord_webhook: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return the cached Settings instance."""
    return Settings()
