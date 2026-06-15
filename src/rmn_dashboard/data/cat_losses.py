"""Cat-loss estimates — load, validate, query.

The bundled YAML at ``cat_loss_estimates.yaml`` is the source of truth
for which post-event modeler loss estimates Panel 7 ("Modeled losses")
considers. Same authorship pattern as ``data/analogs.py`` — Pydantic-
validated, lru-cached, hand-curated by editorial.

Why a separate file (vs. extending ``analogs.py``):

  * Analogs describe *what storms did* (peak winds, landfall point,
    insured-loss aggregate). Cat-loss estimates describe *what the
    modelers said about a storm* — multiple firms, multiple
    refinements, multiple sources. Different schema shape, different
    refresh cadence (analogs lock annually; estimates evolve over
    weeks following each event).
  * Keeping them physically separate prevents a typo in one editorial
    file from breaking the other, and mirrors the analogs ↔ universe
    separation already in place.

Editorial / legal note: numbers from modeler releases are facts and
not copyrightable; surrounding prose is. The schema captures only
factual fields (modeler name, range, issuance date, source URL) and
short editorial refinement notes — multi-paragraph reproduction of
modeler prose belongs nowhere in this file.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# The four major catastrophe modeling firms tracked at launch, plus a
# small set of secondary-but-cited sources. New firms get appended here
# before any new YAML row references them — keeps the editorial bar
# explicit. See docs/post_event_loss_estimates_memo.md §4.
_KNOWN_MODELERS: frozenset[str] = frozenset(
    {
        "Moody's RMS",
        "Verisk",
        "CoreLogic",
        "Karen Clark & Company",
        # Secondary sources cited when they enter the public conversation:
        "PCS",  # subscription-gated; we surface PCS numbers via secondary press
        "Aon",  # event-bulletin commentary; not a model output per se
        "Munich Re NatCat",  # post-season aggregation, not per-event point estimate
    }
)


class CatLossEstimate(BaseModel):
    """One modeler's insured-loss estimate for one event.

    A single modeler can have multiple ``CatLossEstimate`` rows for the
    same event when they refine the estimate over time — the latest by
    ``issued_at`` is the current view; earlier rows preserve the
    trajectory.
    """

    model_config = ConfigDict(frozen=True)

    modeler: str = Field(min_length=1)
    low_usd_billions: float = Field(ge=0.0)
    high_usd_billions: float = Field(ge=0.0)
    issued_at: date
    source_url: str | None = None
    refinement_note: str | None = None

    @field_validator("modeler")
    @classmethod
    def _modeler_must_be_known(cls, value: str) -> str:
        if value not in _KNOWN_MODELERS:
            raise ValueError(
                f"modeler {value!r} is not in the known set; "
                f"add to _KNOWN_MODELERS in data/cat_losses.py if intentional"
            )
        return value

    @model_validator(mode="after")
    def _low_must_be_le_high(self) -> CatLossEstimate:
        if self.low_usd_billions > self.high_usd_billions:
            raise ValueError(
                f"low_usd_billions ({self.low_usd_billions}) must be <= "
                f"high_usd_billions ({self.high_usd_billions})"
            )
        return self

    @property
    def midpoint_usd_billions(self) -> float:
        """Mid-point of the modeler's range — the panel's headline number."""
        return (self.low_usd_billions + self.high_usd_billions) / 2.0

    @property
    def is_point_estimate(self) -> bool:
        """True when low == high. KCC publishes point estimates rather
        than ranges; surfacing this distinction matters for the panel
        when computing dispersion across firms."""
        return self.low_usd_billions == self.high_usd_billions


class CatLossEvent(BaseModel):
    """A catastrophe event with one or more modeler estimates."""

    model_config = ConfigDict(frozen=True)

    event_name: str = Field(min_length=1)
    year: int = Field(ge=1990, le=2100)
    estimates: tuple[CatLossEstimate, ...]

    @field_validator("estimates", mode="before")
    @classmethod
    def _coerce_to_tuple(cls, value: object) -> tuple:
        if isinstance(value, list):
            return tuple(value)
        return value  # type: ignore[return-value]

    @field_validator("estimates")
    @classmethod
    def _at_least_one(cls, value: tuple[CatLossEstimate, ...]) -> tuple[CatLossEstimate, ...]:
        if not value:
            raise ValueError("event must have at least one estimate")
        return value

    @field_validator("estimates")
    @classmethod
    def _no_duplicate_issuances(
        cls, value: tuple[CatLossEstimate, ...]
    ) -> tuple[CatLossEstimate, ...]:
        """Same (modeler, issued_at) pair must not appear twice — a
        modeler can refine on different dates, but two entries on the
        same day from the same firm is almost certainly a curation
        typo."""
        seen: set[tuple[str, date]] = set()
        for est in value:
            key = (est.modeler, est.issued_at)
            if key in seen:
                raise ValueError(
                    f"duplicate estimate for modeler {est.modeler!r} on {est.issued_at}"
                )
            seen.add(key)
        return value

    def latest_per_modeler(self) -> tuple[CatLossEstimate, ...]:
        """Return one estimate per modeler — the most recent by
        ``issued_at``. The panel's default view uses this; the full
        ``estimates`` tuple is available for the trajectory view."""
        latest: dict[str, CatLossEstimate] = {}
        for est in self.estimates:
            existing = latest.get(est.modeler)
            if existing is None or est.issued_at > existing.issued_at:
                latest[est.modeler] = est
        # Stable order: alphabetical by modeler so panel rendering doesn't
        # flicker if the YAML is reordered editorially.
        return tuple(sorted(latest.values(), key=lambda e: e.modeler))

    @property
    def consensus_midpoint_usd_billions(self) -> float:
        """Average of latest-per-modeler midpoints. The panel's
        headline number when showing 'modeler consensus' for an event."""
        latest = self.latest_per_modeler()
        if not latest:
            return 0.0
        return sum(e.midpoint_usd_billions for e in latest) / len(latest)

    @property
    def dispersion_usd_billions(self) -> float:
        """Max minus min across latest-per-modeler midpoints. Editorial
        use: when dispersion is wide, that itself is the story."""
        latest = self.latest_per_modeler()
        if len(latest) < 2:
            return 0.0
        mids = [e.midpoint_usd_billions for e in latest]
        return max(mids) - min(mids)


class CatLossEstimates(BaseModel):
    """Top-level shape of ``cat_loss_estimates.yaml``."""

    model_config = ConfigDict(frozen=True)

    version: int = Field(ge=1)
    last_reviewed: date
    events: tuple[CatLossEvent, ...]

    @field_validator("events", mode="before")
    @classmethod
    def _coerce_to_tuple(cls, value: object) -> tuple:
        if isinstance(value, list):
            return tuple(value)
        return value  # type: ignore[return-value]

    @field_validator("events")
    @classmethod
    def _no_duplicate_events(cls, value: tuple[CatLossEvent, ...]) -> tuple[CatLossEvent, ...]:
        seen: set[tuple[str, int]] = set()
        for event in value:
            key = (event.event_name, event.year)
            if key in seen:
                raise ValueError(f"duplicate event entry {event.event_name!r} ({event.year})")
            seen.add(key)
        return value


def _bundled_yaml_path() -> Path:
    return Path(__file__).parent / "cat_loss_estimates.yaml"


@lru_cache(maxsize=4)
def load_cat_losses(path: Path | None = None) -> CatLossEstimates:
    """Load and validate the cat-loss-estimates YAML.

    Defaults to the bundled file inside ``rmn_dashboard.data``. Pass a
    custom path in tests.
    """
    target = path or _bundled_yaml_path()
    with target.open("r", encoding="utf-8") as fp:
        raw = yaml.safe_load(fp)
    if not isinstance(raw, dict):
        raise ValueError(f"{target.name}: top-level YAML must be a mapping")
    return CatLossEstimates.model_validate(raw)
