"""Application configuration, loaded from environment variables via pydantic-settings.

All runtime configuration lives here. Nothing else in the codebase should read
environment variables directly — import ``settings`` from this module instead.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Runtime configuration. Values come from environment or ``.env``."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Runtime ---
    env: str = Field(default="development", description="development | production")
    debug: bool = Field(default=True)
    log_level: str = Field(default="INFO")

    # --- Database ---
    # SQLite for dev, Postgres URL injected by Render in prod.
    database_url: str = Field(default=f"sqlite:///{PROJECT_ROOT}/data/rmn_dashboard.db")

    # --- Kalshi (authenticated API) ---
    kalshi_api_key_id: str | None = Field(default=None)
    kalshi_private_key_path: str | None = Field(default=None)
    kalshi_base_url: str = Field(default="https://api.elections.kalshi.com/trade-api/v2")

    # --- SEC EDGAR ---
    sec_user_agent: str = Field(
        default="Risk Market News research@riskmarketnews.com",
        description="SEC requires a descriptive User-Agent with contact info.",
    )

    # --- Scheduler ---
    scheduler_enabled: bool = Field(default=False, description="Disabled in dev by default.")

    # --- Deployment ---
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000)


@lru_cache
def get_settings() -> Settings:
    """Cached accessor — Settings is immutable at runtime."""
    return Settings()


settings = get_settings()
