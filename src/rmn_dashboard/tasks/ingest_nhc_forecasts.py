"""NHC forecast-product ingestion task — persist a forecast snapshot per storm.

For every ``Storm`` with ``status='active'``, this task:

  1. Looks up the most recent ``StormObservation`` to find the URLs that
     the Day 9 ingest captured in ``advisory_urls``.
  2. Fetches the ``forecastTrack`` ZIP, parses it with
     :mod:`rmn_dashboard.scrapers.nhc_shapefiles`, and UPSERTs a
     ``Forecast`` row keyed on ``(storm_id, issued_at)``.
  3. Fetches the ``windSpeedProbabilitiesGIS`` ZIP (basin-scoped, so we
     dedupe fetches by URL within a single tick) and attaches the parsed
     GeoJSON to the same ``Forecast`` row.

Design choices worth keeping:

* **Three scheduler jobs, not a monolith.** Day 8 did Kalshi, Day 9
  did NHC observations, Day 10 does NHC forecasts. Keeping them
  separate lets ops tune cadences (forecasts only change on NHC
  advisory boundaries, every 3-6 hours) and lets one ZIP-parsing
  failure not take down the observation stream.
* **UPSERT on (storm_id, issued_at), not insert-then-catch.** The
  pre-insert SELECT lets us absorb repeat polls within the same
  advisory without aborting the whole transaction on a unique-
  constraint conflict. The DB-level constraint (Day 10 migration
  ``e7f9b2c6d1a3``) is still there as belt-and-suspenders.
* **wsp dedupe keyed by URL, not by basin.** NHC's
  ``windSpeedProbabilitiesGIS.zipFile`` is the same string for every
  active Atlantic storm in a given tick, so URL identity is a fine
  proxy. Falls back gracefully if a storm has a different wsp URL
  (theoretically possible; in practice never seen).

Runnable two ways:

    # From Python / APScheduler:
    from rmn_dashboard.tasks.ingest_nhc_forecasts import run_nhc_forecast_ingest
    run_nhc_forecast_ingest(db_session)

    # From the CLI (Render Shell, local):
    python -m rmn_dashboard.tasks.ingest_nhc_forecasts
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from rmn_dashboard.models import Forecast, Storm, StormObservation
from rmn_dashboard.scrapers.nhc_shapefiles import (
    NHCShapefileError,
    ForecastTrack,
    fetch_forecast_track,
    fetch_wind_probability,
)

logger = logging.getLogger(__name__)


# Keys inside ``StormObservation.advisory_urls`` that matter to this task.
# Must match the whitelist in ``scrapers/nhc.py`` — if that changes,
# check here too.
_FORECAST_TRACK_KEY = "forecastTrack"
_WSP_KEY = "windSpeedProbabilitiesGIS"


def _latest_observation_for(db: Session, storm_id: int) -> StormObservation | None:
    """Return the most recent ``StormObservation`` for a storm, or None.

    A storm flagged active but without any observation rows is a corner
    case — e.g. a dev DB populated by hand, or an ingest that upserted
    the Storm then crashed before flushing observations. We treat that
    as "skip, log, move on" rather than raising.
    """
    return db.scalar(
        select(StormObservation)
        .where(StormObservation.storm_id == storm_id)
        .order_by(StormObservation.observation_time.desc())
        .limit(1)
    )


def _extract_zip_url(advisory_urls: dict[str, Any] | None, key: str) -> str | None:
    """Pull a ZIP URL out of a storm's captured ``advisory_urls`` dict.

    NHC's per-product sub-objects carry different shapes (``zipFile``,
    ``url``, ``kmzFile``…). For the products we care about here,
    ``zipFile`` is the shapefile archive we want.
    """
    if not advisory_urls:
        return None
    product = advisory_urls.get(key)
    if not isinstance(product, dict):
        return None
    url = product.get("zipFile")
    return url if isinstance(url, str) and url else None


def _upsert_forecast(
    db: Session,
    storm: Storm,
    forecast_track: ForecastTrack,
    wind_probability_geojson: dict[str, Any] | None,
    source_url: str,
) -> bool:
    """Insert or refresh the ``Forecast`` row for this advisory.

    Returns True if a new row was inserted, False if an existing row was
    updated (or left untouched). The ingest counts both as "processed"
    and logs them separately.
    """
    existing = db.scalar(
        select(Forecast).where(
            Forecast.storm_id == storm.id,
            Forecast.issued_at == forecast_track.issued_at,
        )
    )
    if existing is None:
        db.add(
            Forecast(
                storm_id=storm.id,
                issued_at=forecast_track.issued_at,
                cone_geojson=forecast_track.cone_geojson,
                forecast_5day_points=forecast_track.forecast_5day_points,
                wind_probability_geojson=wind_probability_geojson,
                raw_source_url=source_url,
            )
        )
        return True

    # Existing row: refresh spatial products in case NHC republished
    # the same advisory with corrected geometries (rare, but observed).
    existing.cone_geojson = forecast_track.cone_geojson
    existing.forecast_5day_points = forecast_track.forecast_5day_points
    if wind_probability_geojson is not None:
        existing.wind_probability_geojson = wind_probability_geojson
    existing.raw_source_url = source_url
    return False


def run_nhc_forecast_ingest(
    db: Session,
    http_client: httpx.Client | None = None,
) -> int:
    """Fetch + persist forecast products for every active storm.

    Returns the number of ``Forecast`` rows inserted (refreshed rows do
    not count). No active storms → returns 0 with an INFO log; this is
    the steady state outside hurricane season.

    A single storm's ZIP fetch / parse failure is logged and skipped —
    we commit whatever forecasts we did successfully parse rather than
    losing the whole batch to one upstream glitch.
    """
    active_storms = db.scalars(
        select(Storm).where(Storm.status == "active").order_by(Storm.id)
    ).all()
    if not active_storms:
        logger.info("NHC forecast ingest: no active storms; nothing to fetch.")
        return 0

    inserted = 0
    refreshed = 0
    skipped = 0
    wsp_cache: dict[str, dict[str, Any] | None] = {}

    for storm in active_storms:
        latest_obs = _latest_observation_for(db, storm.id)
        if latest_obs is None:
            logger.warning(
                "NHC forecast ingest: storm %s has no observations; skipping",
                storm.nhc_id,
            )
            skipped += 1
            continue

        track_url = _extract_zip_url(latest_obs.advisory_urls, _FORECAST_TRACK_KEY)
        if track_url is None:
            logger.warning(
                "NHC forecast ingest: storm %s latest observation has no "
                "forecastTrack.zipFile; skipping",
                storm.nhc_id,
            )
            skipped += 1
            continue

        try:
            forecast_track = fetch_forecast_track(track_url, http_client=http_client)
        except (httpx.HTTPError, NHCShapefileError):
            logger.exception(
                "NHC forecast ingest: failed to fetch/parse forecastTrack for %s (%s)",
                storm.nhc_id,
                track_url,
            )
            skipped += 1
            continue

        # wsp is basin-scoped — dedupe fetches by URL across storms in
        # the same tick. A per-URL failure caches None and lets us keep
        # going without retrying the dead URL repeatedly.
        wsp_url = _extract_zip_url(latest_obs.advisory_urls, _WSP_KEY)
        wind_probability_geojson: dict[str, Any] | None = None
        if wsp_url is not None:
            if wsp_url not in wsp_cache:
                try:
                    wsp_cache[wsp_url] = fetch_wind_probability(
                        wsp_url, http_client=http_client
                    )
                except (httpx.HTTPError, NHCShapefileError):
                    logger.exception(
                        "NHC forecast ingest: failed to fetch/parse wsp for %s (%s)",
                        storm.nhc_id,
                        wsp_url,
                    )
                    wsp_cache[wsp_url] = None
            wind_probability_geojson = wsp_cache[wsp_url]

        was_inserted = _upsert_forecast(
            db,
            storm,
            forecast_track,
            wind_probability_geojson,
            track_url,
        )
        if was_inserted:
            inserted += 1
        else:
            refreshed += 1

    db.commit()
    logger.info(
        "NHC forecast ingest: %d storms processed, %d new forecasts inserted, "
        "%d refreshed, %d skipped.",
        len(active_storms),
        inserted,
        refreshed,
        skipped,
    )
    return inserted


def _cli() -> int:
    """Stand-alone entry point — builds its own session and logging config."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from rmn_dashboard.database import SessionLocal

    db = SessionLocal()
    try:
        count = run_nhc_forecast_ingest(db)
        print(f"Persisted {count} new NHC forecast rows.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(_cli())
