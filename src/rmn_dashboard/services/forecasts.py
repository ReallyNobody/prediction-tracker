"""Forecast read helpers — shape the Storm + Observation + Forecast join
into a dashboard-friendly payload.

The view layer (Panel 1 on the dashboard, plus the JSON API) needs a
single document per active storm containing:

  * storm identity + season summary (from ``Storm``)
  * current position + intensity (from the latest ``StormObservation``)
  * latest forecast products (from the latest ``Forecast``): cone polygon,
    5-day track points, optional wind-probability GeoJSON

Three tables, three "most recent" lookups per storm. This module hides
that query dance from the route handlers so they stay focused on request
wiring.

Returns plain dicts rather than ORM objects because the caller is a JSON
endpoint — no lazy-loading surprises across session boundaries, no
pydantic boilerplate for a single-shot response.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from rmn_dashboard.models import Forecast, Storm, StormObservation

# NHC publishes Atlantic + Eastern Pacific + Central Pacific in the same
# activeStorms feed. The scraper ingests all of them; this service filters
# at the read layer so the basin choice is editorial, not data-shape, and
# can be changed without a re-scrape. nhc_id prefixes are 'al' (Atlantic),
# 'ep' (Eastern Pacific), 'cp' (Central Pacific) — always two ASCII letters
# at the start of the canonical id string.
_DEFAULT_BASINS: tuple[str, ...] = ("al",)


def active_storm_forecasts(
    db: Session,
    *,
    include_wsp: bool = False,
    basins: tuple[str, ...] = _DEFAULT_BASINS,
) -> list[dict[str, Any]]:
    """Return one payload per active storm with its latest forecast.

    ``include_wsp=False`` by default: the wind-probability GeoJSON can run
    into the megabytes for a full Atlantic basin fetch, and Panel 1 (the
    cone map) doesn't use it. Panel 4 (landfall probability) passes
    ``include_wsp=True``.

    ``basins`` defaults to Atlantic-only. NHC's ``CurrentStorms.json`` feed
    publishes Atlantic + Eastern Pacific + Central Pacific in a single
    payload; the dashboard's editorial frame (launch piece, off-season
    fallback image, empty-state copy) is Atlantic-only, so we filter at
    read time. Pass ``basins=("al", "ep")`` to include EP, or ``basins=()``
    to disable the filter entirely. Matching is case-insensitive — the
    NHC payload uses lowercase ids ('ep032026') while existing seed data
    and tests use uppercase ('AL112017').

    A storm with ``status == 'active'`` but no ``Forecast`` row yet —
    e.g. a brand-new Potential Cyclone whose forecastTrack zip hasn't
    been parsed yet — is *included* with ``forecast: null``. The
    client-side cone renderer handles that case by drawing just the
    current-position marker (no cone polygon, no 5-day track points).

    History: through 2026-06-16 this service silently skipped storms
    without forecast geometry. That was editorially wrong: a brand-new
    NHC advisory for a PC making US landfall is exactly the moment the
    dashboard should be most useful, and the silent-skip hid it for
    the 15-30 minutes between the storm's first activeStorms appearance
    and the next shapefile-scraper tick. PTC One (al012026) caught
    this on June 16, 2026.

    Ordering: storms are returned sorted by ``nhc_id`` so the panel UI
    has a stable order across polls. When two storms are active at once
    (not uncommon at peak season), the first one in the list is the
    lower-numbered basin designator.
    """
    query = select(Storm).where(Storm.status == "active")
    if basins:
        basin_clauses = [Storm.nhc_id.ilike(f"{b}%") for b in basins]
        query = query.where(basin_clauses[0] if len(basin_clauses) == 1 else or_(*basin_clauses))
    active = db.scalars(query.order_by(Storm.nhc_id)).all()
    if not active:
        return []

    payloads: list[dict[str, Any]] = []
    for storm in active:
        latest_obs = db.scalar(
            select(StormObservation)
            .where(StormObservation.storm_id == storm.id)
            .order_by(StormObservation.observation_time.desc())
            .limit(1)
        )
        latest_forecast = db.scalar(
            select(Forecast)
            .where(Forecast.storm_id == storm.id)
            .order_by(Forecast.issued_at.desc())
            .limit(1)
        )

        # latest_forecast may be None — _build_payload handles that by
        # emitting ``forecast: null``. The client-side renderer draws
        # current-position only in that case. See docstring for why we
        # used to skip and why that was wrong.
        payloads.append(_build_payload(storm, latest_obs, latest_forecast, include_wsp))

    return payloads


def _build_payload(
    storm: Storm,
    observation: StormObservation | None,
    forecast: Forecast | None,
    include_wsp: bool,
) -> dict[str, Any]:
    """Assemble one storm's response payload.

    Both ``observation`` and ``forecast`` are nullable. The "active
    storm, no observations yet" corner case exists when a dev DB is
    seeded by hand; the "active storm, no forecast yet" case is
    routine for brand-new NHC advisories whose shapefile-scrape hasn't
    completed (see ``active_storm_forecasts`` docstring). When forecast
    is None, the payload emits ``forecast: null`` and the client-side
    renderer draws only the current-position marker.
    """
    forecast_block: dict[str, Any] | None
    if forecast is None:
        forecast_block = None
    else:
        forecast_block = {
            "issued_at": _isoformat(forecast.issued_at),
            "cone_geojson": forecast.cone_geojson,
            "forecast_5day_points": forecast.forecast_5day_points,
            "raw_source_url": forecast.raw_source_url,
        }
        if include_wsp:
            forecast_block["wind_probability_geojson"] = forecast.wind_probability_geojson

    current_position: dict[str, Any] | None = None
    if observation is not None:
        current_position = {
            "latitude_deg": observation.latitude_deg,
            "longitude_deg": observation.longitude_deg,
            "classification": observation.classification,
            "intensity_kt": observation.intensity_kt,
            "pressure_mb": observation.pressure_mb,
            "movement_dir_deg": observation.movement_dir_deg,
            "movement_speed_mph": observation.movement_speed_mph,
            "observation_time": _isoformat(observation.observation_time),
        }

    return {
        "storm": {
            "nhc_id": storm.nhc_id,
            "name": storm.name,
            "season_year": storm.season_year,
            "storm_type": storm.storm_type,
            "max_wind_kt": storm.max_wind_kt,
            "min_pressure_mb": storm.min_pressure_mb,
            "status": storm.status,
        },
        "current_position": current_position,
        "forecast": forecast_block,
    }


def _isoformat(value: Any) -> str | None:
    """Safely ISO-format a datetime; return None for None.

    SQLite round-trips ``DateTime(timezone=True)`` as naive — we don't
    paper over that here, we just hand it to ``isoformat()`` which is
    happy with either. The client sees a naive-looking string in dev
    (SQLite) and a tz-aware one in prod (Postgres); both are parseable
    by ``Date.parse`` on the JS side.
    """
    if value is None:
        return None
    return value.isoformat()
