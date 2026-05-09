"""Hurricane equity universe — load, validate, query.

The bundled YAML at ``hurricane_universe.yaml`` is the source of truth
for which public companies Panel 2 tracks. This module turns it into
typed Python objects and exposes a couple of small query helpers
(``tickers_for_states``, ``filter_by_sector``) that the service layer
and the cone-overlap highlight code consume.

Why Pydantic, not raw dicts:

  * The YAML is hand-edited; a typo like ``sector: insure`` instead of
    ``insurer`` should fail loud at import time, not silently render a
    blank pill in the UI.
  * Validation rules ("``key_states`` must be 2-letter US state codes")
    encode editorial rules as code so a future contributor can't drift.

Why a module-level cache:

  * Loading + validating a 35-row YAML is fast (~ms) but happens on
    every page render that calls into the service layer. ``lru_cache``
    keeps the loader idempotent and free.
  * Tests that need a freshly-loaded universe call ``load_universe.cache_clear()``.

The validator deliberately does NOT verify CIKs against SEC EDGAR — the
CIK column is null in this file by design (resolved post-launch by a
discovery job). Adding network validation here would couple module
import to network availability.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Two-letter postal codes for the 50 states + DC + PR + USVI. PR / USVI
# are included because they're hurricane-exposed and a future ticker
# entry might legitimately list them (e.g. a Caribbean utility).
_VALID_US_STATES: frozenset[str] = frozenset(
    {
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "DC",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
        "PR",
        "VI",
    }
)

# Day 15 added "cat_bond_etf" so a publicly-traded catastrophe-bond ETF
# can ride the same ingest plumbing as the equity universe. It belongs
# here (not in a separate YAML) because the data shape and scrape path
# are identical — the only difference is which panel reads it.
#
# Day 20 added "pc_index" — KBW P&C Insurance ETF (KBWP). Same logic as
# cat_bond_etf: a publicly-traded index proxy, ingested through the
# yfinance plumbing, but editorially distinct from per-name equities.
# Panel 2 filters BOTH cat_bond_etf and pc_index out of its equity grid;
# Panel 3 ("Hurricane risk capital") selects both as two rows of the
# same readout. There's no clean US-listed reinsurance index ETF — KBW
# publishes a Global Reinsurance Index but its investable trackers are
# foreign-listed and thin — so reinsurance stays as the individual
# tickers in the equity universe (RNR, EG, ACGL, AXS, MKL, HG).
# Day 40 added "lng" — Gulf Coast LNG export infrastructure (Cheniere /
# Sempra Infrastructure / Energy Transfer). Editorially distinct from
# regulated utilities: a hurricane that shuts a Cameron LNG terminal
# moves global gas prices, not just the local rate base. Kept as its
# own sector so Panel 2 can group / filter / badge LNG separately and
# the new XLU-spread computation only applies to operationally-exposed
# energy names (utility + lng), not to insurers or homebuilders.
#
# Day 40 also added "benchmark" — the XLU sector ETF acts as the spread
# baseline for utility / LNG ticker performance. Same data-shape as
# cat_bond_etf and pc_index (rides the yfinance ingest plumbing) but
# editorially invisible: never appears as a Panel 2 tile, only fuels
# the "vs XLU" spread badge on utility / LNG rows.
Sector = Literal[
    "insurer",
    "reinsurer",
    "homebuilder",
    "utility",
    "lng",
    "cat_bond_etf",
    "pc_index",
    "benchmark",
]
Relevance = Literal["high", "medium", "low"]


class UniverseEntry(BaseModel):
    """One row of the hurricane equity universe."""

    model_config = ConfigDict(frozen=True)

    ticker: str = Field(min_length=1, max_length=10)
    name: str = Field(min_length=1)
    sector: Sector
    cik: str | None = None
    key_states: tuple[str, ...] = Field(default_factory=tuple)
    hurricane_relevance: Relevance
    notes: str = ""

    @field_validator("ticker")
    @classmethod
    def _ticker_is_uppercase_alphanumeric(cls, value: str) -> str:
        # NYSE / Nasdaq tickers are uppercase A-Z + sometimes a class
        # suffix (BRK.B). Reject lowercase to keep the YAML editorially
        # tidy — auto-uppercasing would mask typos like 'uve' that
        # might otherwise be a different intended symbol.
        if not value.replace(".", "").replace("-", "").isalnum():
            raise ValueError(f"ticker {value!r} contains unexpected characters")
        if value != value.upper():
            raise ValueError(f"ticker {value!r} must be uppercase")
        return value

    @field_validator("key_states", mode="before")
    @classmethod
    def _normalize_states(cls, value: object) -> tuple[str, ...]:
        # YAML parses the inline list ``[FL, TX]`` as a Python list; we
        # convert to a tuple here so the model is hashable / frozen.
        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            raise ValueError(f"key_states must be a list, got {type(value).__name__}")
        states = tuple(value)
        for state in states:
            if not isinstance(state, str):
                raise ValueError(f"key_states entries must be strings, got {state!r}")
            if state not in _VALID_US_STATES:
                raise ValueError(
                    f"key_states entry {state!r} is not a recognized US state code "
                    "(use the 2-letter postal abbreviation)"
                )
        return states

    @field_validator("cik")
    @classmethod
    def _cik_is_digits_or_none(cls, value: str | None) -> str | None:
        # The YAML can leave CIK as null (typical first-pass) or fill in
        # the canonical 10-digit zero-padded form once the discovery job
        # has resolved it. Accept either; reject malformed strings so a
        # future hand-edit can't paste a URL by accident.
        if value is None:
            return None
        if not value.isdigit():
            raise ValueError(f"cik {value!r} must be all digits or null")
        return value


class Universe(BaseModel):
    """Top-level shape of ``hurricane_universe.yaml``."""

    model_config = ConfigDict(frozen=True)

    version: int = Field(ge=1)
    last_reviewed: date
    tickers: tuple[UniverseEntry, ...]

    @field_validator("tickers", mode="before")
    @classmethod
    def _coerce_to_tuple(cls, value: object) -> tuple:
        if isinstance(value, list):
            return tuple(value)
        return value  # type: ignore[return-value]

    @field_validator("tickers")
    @classmethod
    def _no_duplicate_tickers(cls, value: tuple[UniverseEntry, ...]) -> tuple[UniverseEntry, ...]:
        seen: set[str] = set()
        for entry in value:
            if entry.ticker in seen:
                raise ValueError(f"duplicate ticker {entry.ticker!r} in universe YAML")
            seen.add(entry.ticker)
        return value


# ----- Loader -------------------------------------------------------------


def _bundled_yaml_path() -> Path:
    """Resolve the path to the YAML file shipped with the package."""
    return Path(__file__).parent / "hurricane_universe.yaml"


@lru_cache(maxsize=4)
def load_universe(path: Path | None = None) -> Universe:
    """Load and validate the hurricane equity universe.

    Defaults to the bundled YAML inside ``rmn_dashboard.data``. Pass a
    custom path in tests to exercise validation rules against a hand-
    crafted fixture.

    Cached: subsequent calls with the same path are free. Tests that
    need fresh state should call ``load_universe.cache_clear()``.
    """
    target = path or _bundled_yaml_path()
    with target.open("r", encoding="utf-8") as fp:
        raw = yaml.safe_load(fp)
    if not isinstance(raw, dict):
        raise ValueError(f"{target.name}: top-level YAML must be a mapping")
    return Universe.model_validate(raw)


# ----- Query helpers ------------------------------------------------------


def filter_by_sector(universe: Universe, sectors: Iterable[Sector]) -> tuple[UniverseEntry, ...]:
    """Subset the universe to entries in any of the given sectors."""
    wanted = set(sectors)
    return tuple(entry for entry in universe.tickers if entry.sector in wanted)


def tickers_for_states(universe: Universe, states: Iterable[str]) -> tuple[UniverseEntry, ...]:
    """Universe entries whose ``key_states`` intersect any of ``states``.

    Drives the Panel 2 cone-overlap highlight: when Panel 1 has an
    active forecast, the JS computes which states the cone touches and
    asks the service layer for those exposed tickers.

    Reinsurers — whose ``key_states`` is empty by editorial convention —
    are *never* returned by this filter. That's intentional: per-state
    precision for a global reinsurance book is a fiction we don't want
    to imply by lighting them up on a single-state cone.
    """
    target = {s.upper() for s in states}
    if not target:
        return ()
    return tuple(
        entry
        for entry in universe.tickers
        if entry.key_states and target.intersection(entry.key_states)
    )
