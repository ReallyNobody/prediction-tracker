"""NHC active-storms ingestion task — fetch and persist a poll's worth of observations.

Each call fetches CurrentStorms.json, then for each live storm:

  * get-or-creates the ``Storm`` identity row, keyed by ``nhc_id``
    (UPSERT semantics on name, storm_type, lifetime max wind, lifetime
    min pressure, and status);
  * inserts a ``StormObservation`` snapshot row keyed by
    ``(storm_id, observation_time)``, skipping duplicates when we poll
    more often than NHC issues new advisories.

Runnable two ways:

    # From Python / APScheduler:
    from rmn_dashboard.tasks.ingest_nhc import run_nhc_ingest
    run_nhc_ingest(db_session)

    # From the CLI (Render Shell, local):
    python -m rmn_dashboard.tasks.ingest_nhc

The CLI wrapper builds its own session and closes it; the library
function takes a session so callers (tests, scheduler) can control
lifecycle.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from rmn_dashboard.models import Storm, StormObservation
from rmn_dashboard.scrapers.nhc import fetch_active_storms

if TYPE_CHECKING:
    from rmn_dashboard.scrapers.nhc import NHCStormObservation

logger = logging.getLogger(__name__)


# Mapping from NHC classification codes (as they appear verbatim in
# ``CurrentStorms.json``) to the human-readable string shown on the
# Storm row. The Observation row keeps the raw code so the dashboard can
# render whatever phrasing it prefers at display time.
# Reference: NHC Tropical Cyclone Status JSON File Reference, §"classification".
_CLASSIFICATION_HUMAN: dict[str, str] = {
    "HU": "Hurricane",
    "TS": "Tropical Storm",
    "TD": "Tropical Depression",
    "STD": "Subtropical Depression",
    "STS": "Subtropical Storm",
    "PTC": "Post-Tropical Cyclone",
    "TY": "Typhoon",
    "PC": "Post-Cyclone",
}


def _expand_classification(code: str) -> str:
    """Translate an NHC classification code into a display string.

    Unknown codes fall through as-is — better to show the raw code than
    to silently drop information when NHC introduces a new category.
    """
    return _CLASSIFICATION_HUMAN.get(code, code)


def _season_year_from_nhc_id(nhc_id: str) -> int:
    """Extract the 4-digit season year from an NHC id like ``'al112017'``.

    NHC ids are ``<basin:2><number:2><year:4>`` = 8 chars. If the shape
    doesn't match we raise — this is identity-critical and silent wrong
    values would corrupt reporting for an entire season.
    """
    if len(nhc_id) != 8 or not nhc_id[-4:].isdigit():
        raise ValueError(
            f"NHC id doesn't match expected shape (8 chars ending in 4-digit year): {nhc_id!r}"
        )
    return int(nhc_id[-4:])


def _upsert_storm(db: Session, obs: NHCStormObservation) -> Storm:
    """Return a ``Storm`` row for this observation's ``nhc_id``, creating
    or refreshing it as needed.

    Semantics:
      * first sight: INSERT a new Storm with identity + lifetime stats
        seeded from the observation, and status='active';
      * subsequent sight: UPDATE name (so Invest → named-storm renames
        land), refresh storm_type (classification can escalate:
        TD → TS → HU), raise max_wind_kt to the new peak if higher,
        lower min_pressure_mb if deeper, mark status='active'.

    A storm that dissipates falls off ``activeStorms`` entirely, so this
    task never touches post-dissipation rows — the Storm row just stops
    getting refreshed. Setting ``status='dissipated'`` is a future
    sweeper's job (Week 3+).
    """
    human_type = _expand_classification(obs.classification)
    season = _season_year_from_nhc_id(obs.nhc_id)

    existing = db.scalars(select(Storm).where(Storm.nhc_id == obs.nhc_id)).one_or_none()

    if existing is None:
        storm = Storm(
            nhc_id=obs.nhc_id,
            name=obs.name,
            season_year=season,
            storm_type=human_type,
            max_wind_kt=obs.intensity_kt,
            min_pressure_mb=obs.pressure_mb,
            status="active",
        )
        db.add(storm)
        db.flush()  # populate storm.id so the FK insert has a value
        return storm

    existing.name = obs.name
    existing.storm_type = human_type
    if existing.max_wind_kt is None or obs.intensity_kt > existing.max_wind_kt:
        existing.max_wind_kt = obs.intensity_kt
    if obs.pressure_mb is not None and (
        existing.min_pressure_mb is None or obs.pressure_mb < existing.min_pressure_mb
    ):
        existing.min_pressure_mb = obs.pressure_mb
    existing.status = "active"
    return existing


def _observation_exists(db: Session, storm_id: int, observation_time: object) -> bool:
    """True if we already have a StormObservation row for this (storm, time).

    Cheaper than relying on the DB-level unique constraint to raise —
    one extra SELECT per storm per tick, but keeps the batch inside a
    single commit (INTEGRITY error would abort the whole transaction
    and we'd lose any legitimate new rows that were about to commit).
    """
    return (
        db.scalar(
            select(StormObservation.id).where(
                StormObservation.storm_id == storm_id,
                StormObservation.observation_time == observation_time,
            )
        )
        is not None
    )


def _observation_to_row(obs: NHCStormObservation, storm: Storm) -> StormObservation:
    """Map the scraper's dataclass onto a ``StormObservation`` row.

    ``ingested_at`` is populated by the DB default (``func.now()``) so
    every row in a batch carries a consistent server-clock timestamp.
    """
    return StormObservation(
        storm_id=storm.id,
        bin_number=obs.bin_number,
        classification=obs.classification,
        intensity_kt=obs.intensity_kt,
        pressure_mb=obs.pressure_mb,
        latitude_deg=obs.latitude_deg,
        longitude_deg=obs.longitude_deg,
        movement_dir_deg=obs.movement_dir_deg,
        movement_speed_mph=obs.movement_speed_mph,
        observation_time=obs.last_update,
        advisory_urls=obs.advisory_urls or None,
    )


def run_nhc_ingest(
    db: Session,
    http_client: httpx.Client | None = None,
) -> int:
    """Fetch NHC active storms and persist a snapshot per storm. Returns the
    number of ``StormObservation`` rows inserted.

    Transaction shape: single commit at the end. A partial scrape that
    returns 2 of 3 storms still persists what it got — the scraper's own
    per-record try/except means an upstream malformation log-and-continues,
    and we treat whatever comes back as authoritative for this run.

    Empty ``activeStorms`` is a normal off-season state: zero rows inserted,
    no warning, no error.
    """
    observations = fetch_active_storms(http_client=http_client)
    if not observations:
        logger.info("NHC ingest: no active storms; nothing to persist.")
        return 0

    inserted = 0
    skipped_duplicate = 0
    for obs in observations:
        try:
            storm = _upsert_storm(db, obs)
        except ValueError:
            logger.exception("NHC ingest: skipping storm with malformed id: %s", obs.nhc_id)
            continue

        if _observation_exists(db, storm.id, obs.last_update):
            skipped_duplicate += 1
            continue

        db.add(_observation_to_row(obs, storm))
        inserted += 1

    db.commit()
    logger.info(
        "NHC ingest: %d storms observed, %d new observations persisted (%d duplicates skipped).",
        len(observations),
        inserted,
        skipped_duplicate,
    )
    return inserted


def _cli() -> int:
    """Stand-alone entry point — builds its own session and logging config."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from rmn_dashboard.database import SessionLocal

    db = SessionLocal()
    try:
        count = run_nhc_ingest(db)
        print(f"Persisted {count} NHC storm observation snapshots.")
        # Return 0 even on count==0 — off-season is a valid success.
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(_cli())
