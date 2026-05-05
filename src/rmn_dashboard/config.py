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
    # Named ``app_env`` rather than ``env`` so pydantic-settings picks up the
    # conventional ``APP_ENV`` environment variable (Render, Heroku, many
    # others). A bare ``env`` field would map to ``ENV``, which nothing sets.
    app_env: str = Field(default="development", description="development | production")
    debug: bool = Field(default=True)
    log_level: str = Field(default="INFO")

    # --- Database ---
    # SQLite for dev, Postgres URL injected by Render in prod.
    database_url: str = Field(default=f"sqlite:///{PROJECT_ROOT}/data/rmn_dashboard.db")

    # --- Kalshi (authenticated API) ---
    kalshi_api_key_id: str | None = Field(default=None)
    kalshi_private_key_path: str | None = Field(default=None)
    kalshi_base_url: str = Field(default="https://api.elections.kalshi.com/trade-api/v2")

    # --- Polymarket (public Gamma API; no auth) ---
    # Day 37 added Polymarket alongside Kalshi as a second prediction-market
    # source for Panel 4. Gamma is free and public — no API key, no signed
    # requests, just rate-courteous polling. URL is configurable for future
    # mock-server testing or Polymarket region splits.
    polymarket_base_url: str = Field(
        default="https://gamma-api.polymarket.com",
        description="Polymarket Gamma API base URL.",
    )

    # --- SEC EDGAR ---
    sec_user_agent: str = Field(
        default="Risk Market News research@riskmarketnews.com",
        description="SEC requires a descriptive User-Agent with contact info.",
    )

    # --- NHC ---
    # Atlantic & East-Pacific tropical-cyclone status feed. Unauthenticated,
    # no documented rate limit; poll every 15 minutes in prod. Also reuses
    # ``sec_user_agent`` as a courtesy User-Agent — NHC doesn't require one.
    nhc_current_storms_url: str = Field(
        default="https://www.nhc.noaa.gov/CurrentStorms.json",
        description="NHC active-storms JSON feed.",
    )

    # --- Scheduler ---
    scheduler_enabled: bool = Field(default=False, description="Disabled in dev by default.")
    kalshi_ingest_interval_minutes: int = Field(
        default=15,
        ge=1,
        description="How often the scheduled Kalshi ingest job fires (minutes).",
    )
    nhc_ingest_interval_minutes: int = Field(
        default=15,
        ge=1,
        description="How often the scheduled NHC active-storms ingest fires (minutes).",
    )
    nhc_forecast_ingest_interval_minutes: int = Field(
        default=30,
        ge=1,
        description=(
            "How often the scheduled NHC forecast-product ingest fires (minutes). "
            "Defaults longer than the observation cadence because NHC only "
            "republishes forecast shapefiles on advisory boundaries (every "
            "3–6 hours) — more frequent polling burns bandwidth for no "
            "new data."
        ),
    )
    yfinance_ingest_interval_minutes: int = Field(
        default=15,
        ge=1,
        description=(
            "How often the scheduled yfinance equity-quote ingest fires "
            "(minutes). Yahoo Finance is ~15 min delayed regardless, so a "
            "shorter cadence than 15 minutes is wasted bandwidth — match "
            "the delay window."
        ),
    )
    polymarket_ingest_interval_minutes: int = Field(
        default=15,
        ge=1,
        description=(
            "How often the scheduled Polymarket Gamma ingest fires "
            "(minutes). Parallel to the Kalshi cadence — Polymarket has "
            "no documented rate limit but we're a courtesy guest on a "
            "public endpoint. 15 min keeps the two prediction-market "
            "sources in temporal lockstep so Panel 4's combined "
            "freshness reads consistently."
        ),
    )

    # --- Deployment ---
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000)


@lru_cache
def get_settings() -> Settings:
    """Cached accessor — Settings is immutable at runtime."""
    return Settings()


settings = get_settings()
