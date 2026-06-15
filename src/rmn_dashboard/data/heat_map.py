"""Prediction-market heat-map — canonical question registry.

The bundled YAML at ``prediction_market_questions.yaml`` is the source
of truth for Panel 8's cross-platform heat-map. Each canonical question
captures one hurricane outcome and which platform-specific ticker
represents it on Kalshi, Polymarket (and, eventually, PredictIt).

Why a separate file from ``data/cat_losses.py``:

  * Cat-loss estimates describe modeler output on completed events.
    Canonical market questions describe live trading instruments —
    different update cadence (continuous vs. weeks-long settlement
    refinement), different lifecycle (markets close; events resolve and
    stay resolved), different editorial bar (a typo'd Kalshi ticker
    silently returns no data; a typo'd modeler name is rejected
    upfront).
  * Heat-map matching is structurally separate from analog and modeler
    work — keeping the file dedicated lets us evolve the platform
    mapping (add PredictIt rows, retire stale slugs) without churning
    files that pertain to other panels.

Schema design notes:

  * ``platforms`` is a dict so the YAML reads naturally — editorial
    sees ``platforms: {kalshi: ..., polymarket: ...}`` rather than a
    list of two-key objects. Pydantic validates the keys against
    ``_KNOWN_PLATFORMS`` so a typo (``"polymarekt"``) fails on load
    rather than emitting a silently-empty row at render time.
  * ``category`` is a closed set — adding a new category to
    ``_KNOWN_CATEGORIES`` is a deliberate editorial act, not something
    you can do by reflex while writing a YAML row. The heat-map UI may
    eventually group columns by category, so the closed set keeps the
    grouping vocabulary stable.
  * ``id`` is slug-shaped — used as the cross-reference key when the
    service writes the per-question cell array. Restricting to
    ``[a-z0-9-]`` keeps URLs and JSON keys predictable.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Closed set of platforms the heat-map can render rows for. Adding
# PredictIt (or any new venue) here is a deliberate act — it means the
# scraper exists and produces ``prediction_markets`` rows that the
# heat-map service can read.
_KNOWN_PLATFORMS: frozenset[str] = frozenset(
    {
        "kalshi",
        "polymarket",
        # "predictit",  # not integrated at launch — see PREDICTION_MARKETS_GUIDE.md
    }
)

# Closed set of question categories. The heat-map UI may eventually
# group columns visually by category, so the vocabulary stays small and
# editorial. Adding a category is intentional.
_KNOWN_CATEGORIES: frozenset[str] = frozenset(
    {
        "count",  # "N or more named storms" — count thresholds
        "intensity",  # ACE thresholds, major-hurricane (Cat 3+) counts
        "timing",  # "by date X" formation/landfall questions
        "landfall",  # geographic landfall (state, region)
    }
)


class CanonicalQuestion(BaseModel):
    """One canonical hurricane question matched across prediction markets.

    The same outcome may trade on Kalshi as ``KXHURCTOT-26DEC01-T5`` and
    on Polymarket as ``will-there-be-5-or-more-named-storms-in-2026``;
    this row is the editorial assertion that those two tickers represent
    the same question and can sit on the same heat-map column.
    """

    model_config = ConfigDict(frozen=True)

    # Slug used as the heat-map column key in the API payload. Lowercase
    # alphanumeric with hyphens — keeps it URL-safe and stable across
    # editorial label rewrites.
    id: str = Field(min_length=1, pattern=r"^[a-z0-9-]+$")

    # The cell-header label rendered above the column in the heat-map.
    # Short_label caps at 24 chars so it fits in the column header without
    # truncation on standard dashboard widths.
    short_label: str = Field(min_length=1, max_length=24)

    # Long-form label used in the cell hover tooltip — disambiguates
    # questions whose short_label collapses important nuance ("Count ≥5"
    # vs. "Count ≥5 by Aug 1").
    long_label: str = Field(min_length=1)

    category: str

    # Platform → ticker map. Empty dict is rejected — a question without
    # any platform link has no data to display and is editorial noise.
    platforms: dict[str, str]

    @field_validator("category")
    @classmethod
    def _category_must_be_known(cls, value: str) -> str:
        if value not in _KNOWN_CATEGORIES:
            raise ValueError(
                f"category {value!r} is not in the known set; "
                f"add to _KNOWN_CATEGORIES in data/heat_map.py if intentional"
            )
        return value

    @field_validator("platforms")
    @classmethod
    def _platforms_must_be_nonempty_and_known(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("platforms must list at least one platform → ticker mapping")
        for platform, ticker in value.items():
            if platform not in _KNOWN_PLATFORMS:
                raise ValueError(
                    f"platform {platform!r} is not known; "
                    f"add to _KNOWN_PLATFORMS in data/heat_map.py if intentional"
                )
            if not isinstance(ticker, str) or not ticker.strip():
                raise ValueError(f"platform {platform!r} must map to a non-empty ticker string")
        return value

    def link_for(self, platform: str) -> str | None:
        """Return the platform-specific ticker for this question, or
        ``None`` if the platform doesn't carry it. Empty cells in the
        heat-map come from this returning None."""
        return self.platforms.get(platform)


class HeatMapQuestions(BaseModel):
    """Top-level shape of ``prediction_market_questions.yaml``."""

    model_config = ConfigDict(frozen=True)

    version: int = Field(ge=1)
    last_reviewed: date
    questions: tuple[CanonicalQuestion, ...]

    @field_validator("questions", mode="before")
    @classmethod
    def _coerce_to_tuple(cls, value: object) -> tuple:
        if isinstance(value, list):
            return tuple(value)
        return value  # type: ignore[return-value]

    @field_validator("questions")
    @classmethod
    def _at_least_one(cls, value: tuple[CanonicalQuestion, ...]) -> tuple[CanonicalQuestion, ...]:
        if not value:
            raise ValueError("at least one canonical question required")
        return value

    @field_validator("questions")
    @classmethod
    def _no_duplicate_ids(
        cls, value: tuple[CanonicalQuestion, ...]
    ) -> tuple[CanonicalQuestion, ...]:
        seen: set[str] = set()
        for q in value:
            if q.id in seen:
                raise ValueError(f"duplicate canonical question id {q.id!r}")
            seen.add(q.id)
        return value

    def platforms_present(self) -> tuple[str, ...]:
        """Return the platforms that appear across the question set, in
        a stable order. The heat-map renders one row per platform here,
        so platforms with no questions yet are correctly absent (not an
        empty row spanning all columns)."""
        seen: set[str] = set()
        for q in self.questions:
            seen.update(q.platforms.keys())
        # Stable, deterministic ordering — Kalshi first if present (the
        # original integration), then alphabetical for everything else.
        # This keeps the heat-map's row ordering predictable even as we
        # add platforms.
        ordered: list[str] = []
        if "kalshi" in seen:
            ordered.append("kalshi")
            seen.remove("kalshi")
        ordered.extend(sorted(seen))
        return tuple(ordered)

    def questions_for_platform(self, platform: str) -> tuple[CanonicalQuestion, ...]:
        """Return the subset of questions that the given platform
        carries. Used by the service to issue per-platform DB lookups."""
        return tuple(q for q in self.questions if platform in q.platforms)


def _bundled_yaml_path() -> Path:
    return Path(__file__).parent / "prediction_market_questions.yaml"


@lru_cache(maxsize=4)
def load_heat_map_questions(path: Path | None = None) -> HeatMapQuestions:
    """Load and validate the canonical-question YAML.

    Defaults to the bundled file inside ``rmn_dashboard.data``. Pass a
    custom path in tests; the lru_cache treats different paths as
    different inputs."""
    target = path or _bundled_yaml_path()
    with target.open("r", encoding="utf-8") as fp:
        raw = yaml.safe_load(fp)
    if not isinstance(raw, dict):
        raise ValueError(f"{target.name}: top-level YAML must be a mapping")
    return HeatMapQuestions.model_validate(raw)
