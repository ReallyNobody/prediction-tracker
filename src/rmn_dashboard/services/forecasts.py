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

from sqlalchemy import select
from sqlalchemy.orm import Session

from rmn_dashboard.models import Forecast, Storm, StormObservation


def active_storm_forecasts(
    db: Session,
    *,
    include_wsp: bool = False,
) -> list[dict[str, Any]]:
    """Return one payload per active storm with its latest forecast.

    ``include_wsp=False`` by default: the wind-probability GeoJSON can run
    into the megabytes for a full Atlantic basin fetch, and Panel 1 (the
    cone map) doesn't use it. Panel 4 (landfall probability) passes
    ``include_wsp=True``.

    A storm with ``status == 'active'`` but no ``Forecast`` row yet — e.g.
    a freshly-ingested Invest that hasn't had its first forecastTrack
    parsed — is *omitted*, not included with a null forecast. The cone
    map has nothing to draw for such a storm; returning it would force
    the UI to guard every field.

    Ordering: storms are returned sorted by ``nhc_id`` so the panel UI
    has a stable order across polls. When two storms are active at once
    (not uncommon at peak season), the first one in the list is the
    lower-numbered basin designator.
    """
    active = db.scalars(select(Storm).where(Storm.status == "active").order_by(Storm.nhc_id)).all()
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

        # No forecast yet → skip. See docstring for why.
        if latest_forecast is None:
            continue

        payloads.append(_build_payload(storm, latest_obs, latest_forecast, include_wsp))

    return payloads


def _build_payload(
    storm: Storm,
    observation: StormObservation | None,
    forecast: Forecast,
    include_wsp: bool,
) -> dict[str, Any]:
    """Assemble one storm's response payload.

    Kept separate so it's easy to unit-test the serialization independent
    of the DB query dance above. The observation is nullable because the
    "active storm, no observations yet" corner case exists (dev DB seeded
    by hand), but the forecast is not — callers must filter those out
    before reaching here.
    """
    forecast_block: dict[str, Any] = {
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
