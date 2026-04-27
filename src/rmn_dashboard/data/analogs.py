"""Historical analogs — load, validate, query.

The bundled YAML at ``historical_analogs.yaml`` is the source of truth
for which past Atlantic hurricanes Panel 5 considers as analogs. Same
authorship pattern as ``data/universe.py`` — Pydantic-validated, lru-
cached, hand-curated by editorial.

Why a separate file (vs. extending ``universe.py``):

  * Universes describe *who is exposed to the risk today*; analogs
    describe *what the risk has done historically*. Different schemas
    (peak_kt + landfall_lat/lon vs. sector + key_states), different
    review cadences (analogs lock annually; universe shifts quarterly).
  * Keeping them physically separate prevents a typo in one editorial
    file from breaking the other.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from rmn_dashboard.data.universe import _VALID_US_STATES


class HistoricalAnalog(BaseModel):
    """One curated historical-analog entry."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    year: int = Field(ge=1900, le=2100)
    peak_kt: int = Field(ge=30, le=200)
    saffir_simpson_at_landfall: int = Field(ge=1, le=5)
    landfall_lat: float = Field(ge=10.0, le=50.0)
    # Western Hemisphere; reject sign mistakes that would put landfall in Asia.
    landfall_lon: float = Field(ge=-100.0, le=-60.0)
    landfall_state: str = Field(min_length=2, max_length=2)
    insured_loss_usd_billions: float = Field(ge=0.0)
    narrative: str = Field(min_length=1)

    @field_validator("landfall_state")
    @classmethod
    def _state_must_be_valid(cls, value: str) -> str:
        if value not in _VALID_US_STATES:
            raise ValueError(f"landfall_state {value!r} is not a recognized US state code")
        return value


class HistoricalAnalogs(BaseModel):
    """Top-level shape of ``historical_analogs.yaml``."""

    model_config = ConfigDict(frozen=True)

    version: int = Field(ge=1)
    last_reviewed: date
    analogs: tuple[HistoricalAnalog, ...]

    @field_validator("analogs", mode="before")
    @classmethod
    def _coerce_to_tuple(cls, value: object) -> tuple:
        if isinstance(value, list):
            return tuple(value)
        return value  # type: ignore[return-value]

    @field_validator("analogs")
    @classmethod
    def _no_duplicate_names(
        cls, value: tuple[HistoricalAnalog, ...]
    ) -> tuple[HistoricalAnalog, ...]:
        seen: set[str] = set()
        for entry in value:
            key = (entry.name, entry.year)
            if key in seen:
                raise ValueError(f"duplicate analog entry {entry.name!r} ({entry.year})")
            seen.add(key)  # type: ignore[arg-type]
        return value


def _bundled_yaml_path() -> Path:
    return Path(__file__).parent / "historical_analogs.yaml"


@lru_cache(maxsize=4)
def load_analogs(path: Path | None = None) -> HistoricalAnalogs:
    """Load and validate the historical-analogs YAML.

    Defaults to the bundled file inside ``rmn_dashboard.data``. Pass a
    custom path in tests.
    """
    target = path or _bundled_yaml_path()
    with target.open("r", encoding="utf-8") as fp:
        raw = yaml.safe_load(fp)
    if not isinstance(raw, dict):
        raise ValueError(f"{target.name}: top-level YAML must be a mapping")
    return HistoricalAnalogs.model_validate(raw)
