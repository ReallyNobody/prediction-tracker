"""Seed Hurricane Irma 2017 (Advisory 36) into the local dev DB.

Purpose: give dashboard developers a cone-map Panel 1 view during the
off-season, without pointing at the live NHC feed. Runs in a second,
idempotent, and refuses to touch a Postgres URL so nobody accidentally
seeds synthetic data into prod.

Why Irma 2017:
  * Advisory 36 (issued 2017-09-07 15:00Z) has a clean 5-day cone that
    covers Cuba through Georgia — it exercises the full Atlantic basin
    footprint the panel is designed to render.
  * Irma peaked at Cat 5 (155 kt), so the popup copy stresses the
    upper end of the intensity ladder.
  * The storm eventually made landfall at Cudjoe Key on 2017-09-10 at
    1300Z — the seed's current-position marker sits just north of Cuba,
    ~36 hours out.

Usage::

    python -m rmn_dashboard.dev.seed_irma
    python -m rmn_dashboard.dev.seed_irma --clear   # drop & re-seed

After running, visit http://localhost:8000/ and Panel 1 should paint
a cone from ~21°N/80°W up through northern Georgia, with five forecast
waypoints along the track.

Shape conventions mirror production::

  * ``Forecast.cone_geojson``         — bare GeoJSON Polygon geometry
                                        (not a Feature, not a FeatureCollection).
  * ``Forecast.forecast_5day_points`` — list of GeoJSON Features with
                                        Point geometries and DBF-style
                                        properties (ADVISNUM, MAXWIND,
                                        FLDATELBL, TCDVLP, ...).
  * ``Storm.status`` = 'active'       — the service layer filters on
                                        this; Irma gets back on the
                                        map for dev purposes even
                                        though in reality she
                                        dissipated on 2017-09-13.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from rmn_dashboard.config import settings
from rmn_dashboard.database import SessionLocal, normalize_database_url
from rmn_dashboard.models import Forecast, Storm, StormObservation

logger = logging.getLogger(__name__)

# ----- Canonical Irma 2017 record -----------------------------------------

NHC_ID = "AL112017"
ADVISORY_ISSUED_AT = datetime(2017, 9, 7, 15, 0, tzinfo=UTC)

# Advisory 36 observation (the "current position" marker on the map).
# Lifted from NHC's TCR for Irma: at 15:00Z on 2017-09-07 she was a
# Cat 5 skirting the northern coast of Cuba.
OBSERVATION = {
    "classification": "HU",
    "intensity_kt": 155,
    "pressure_mb": 924,
    "latitude_deg": 21.8,
    "longitude_deg": -76.3,
    "movement_dir_deg": 280,  # WNW
    "movement_speed_mph": 16,
}

# Prior observation (24h earlier) so Panel 6's day-over-day delta
# logic produces a real "intensified +25 kt" line in dev rather than
# falling back to "newly tracked." Faithful to Irma's actual track:
# at 15:00Z on 2017-09-06 she was a Cat 4 in the central Atlantic
# during her famous rapid-intensification phase, ~24 hours before
# peaking at the 155 kt reading above.
PRIOR_OBSERVATION_TIME = datetime(2017, 9, 6, 15, 0, tzinfo=UTC)
PRIOR_OBSERVATION = {
    "classification": "HU",
    "intensity_kt": 130,
    "pressure_mb": 933,
    "latitude_deg": 18.6,
    "longitude_deg": -57.7,
    "movement_dir_deg": 280,
    "movement_speed_mph": 14,
}

# 5-day forecast track. Timestamps are NHC's official valid-times for
# Advisory 36; positions/intensities are faithful to the original
# product. Each entry becomes one GeoJSON Feature in
# ``Forecast.forecast_5day_points``.
FORECAST_POINTS = [
    # (valid_time, lat, lon, max_wind_kt, stage_code, human_label)
    (datetime(2017, 9, 8, 0, 0, tzinfo=UTC), 22.2, -78.5, 140, "HU", "8/00Z MON"),
    (datetime(2017, 9, 8, 12, 0, tzinfo=UTC), 22.7, -80.3, 130, "HU", "8/12Z MON"),
    (datetime(2017, 9, 9, 0, 0, tzinfo=UTC), 23.3, -81.8, 120, "HU", "9/00Z TUE"),
    (datetime(2017, 9, 9, 12, 0, tzinfo=UTC), 24.5, -82.4, 115, "HU", "9/12Z TUE"),
    (datetime(2017, 9, 10, 12, 0, tzinfo=UTC), 27.0, -82.3, 100, "HU", "10/12Z WED"),
    (datetime(2017, 9, 11, 12, 0, tzinfo=UTC), 30.8, -83.2, 45, "TS", "11/12Z THU"),
    (datetime(2017, 9, 12, 12, 0, tzinfo=UTC), 34.5, -85.0, 25, "TD", "12/12Z FRI"),
]

# 5-day cone polygon. NHC's cone is a "snake" envelope around the
# forecast track, widening with time (historical 2/3-of-all-errors
# radius: ~32nm at 12hr → ~220nm at 120hr). The vertices below trace
# a plausible envelope from Cuba through northern Georgia — tight at
# the near end, fanned out at the far end. Not a verbatim reproduction
# of the Advisory 36 cone (the TCR doesn't publish the vertex table
# for old advisories), but close enough in shape for the dashboard to
# render a realistic picture.
CONE_POLYGON_COORDS = [
    # Trace down the eastern edge (south → north), then back up the
    # western edge. GeoJSON uses [longitude, latitude].
    [-75.8, 21.8],  # start near current position, east side
    [-77.5, 22.2],
    [-79.5, 22.8],
    [-81.0, 23.5],
    [-81.5, 25.5],
    [-80.8, 28.0],
    [-80.5, 30.5],
    [-81.0, 33.5],
    [-82.0, 35.5],  # top-east
    # Top cap
    [-86.0, 35.5],  # top-west
    # West side coming back down
    [-86.5, 33.0],
    [-86.0, 30.5],
    [-85.5, 28.0],
    [-84.5, 25.5],
    [-83.8, 23.5],
    [-82.5, 22.8],
    [-80.5, 22.2],
    [-78.8, 21.8],
    [-75.8, 21.8],  # close ring
]

RAW_SOURCE_URL = (
    "https://www.nhc.noaa.gov/archive/2017/al11/al112017.fstadv.036.shtml?"
    "via=rmn-dashboard-dev-seed"
)


# ----- Guardrails ---------------------------------------------------------


def _require_sqlite() -> None:
    """Refuse to run against a non-SQLite DB.

    The seed is intended for local dev only. Postgres URLs mean Render
    (or Chris's laptop is pointed at prod via DATABASE_URL) — seeding
    synthetic data into either one would muddy real analysis.
    """
    url = normalize_database_url(settings.database_url)
    if not url.startswith("sqlite"):
        raise SystemExit(
            "seed_irma refuses to run against a non-SQLite database "
            f"(DATABASE_URL={settings.database_url!r}). "
            "This seed is for local dev only."
        )


# ----- Payload builders ---------------------------------------------------


def _build_cone_geojson() -> dict:
    """Return the bare GeoJSON Polygon geometry used in production.

    Production's ``Forecast.cone_geojson`` column stores the geometry
    object directly (no Feature wrapper, no FeatureCollection) — the
    service layer hands it through to the JSON API, where
    ``L.geoJSON(cone_geojson)`` renders it. We match that shape here so
    the seed and the real parser are interchangeable from the UI's
    perspective.
    """
    return {
        "type": "Polygon",
        "coordinates": [CONE_POLYGON_COORDS],
    }


def _build_forecast_points() -> list[dict]:
    """Return the 5-day forecast points as GeoJSON Point Features.

    DBF-field naming (ADVISNUM, MAXWIND, FLDATELBL, TCDVLP, LAT, LON)
    mirrors what ``rmn_dashboard.scrapers.nhc_shapefiles`` produces
    from a real NHC ``_5day_pts.shp`` payload. forecast_map.js reads
    FLDATELBL / MAXWIND / TCDVLP for the popup.
    """
    features: list[dict] = []
    for valid_time, lat, lon, max_wind, stage, label in FORECAST_POINTS:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "ADVISNUM": "036",
                    "ADVDATE": ADVISORY_ISSUED_AT.strftime("%y%m%d %H%M"),
                    "LAT": lat,
                    "LON": lon,
                    "MAXWIND": max_wind,
                    "TCDVLP": stage,
                    "FLDATELBL": label,
                    "VALIDTIME": valid_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            }
        )
    return features


# ----- DB writes ----------------------------------------------------------


def _clear_existing(db: Session) -> None:
    """Drop Irma's Storm row (and cascade-deletes its observations +
    forecasts via the FK ondelete=CASCADE).

    Expunges the session identity map afterward so a subsequent
    re-insert doesn't collide with stale in-memory objects. SQLite
    without AUTOINCREMENT reuses ROWIDs, so the fresh row gets the
    same PK as the one we just deleted — and SQLAlchemy warns when
    it flushes a new Python object claiming an identity another
    (still-tracked, now-detached) object already owns.
    """
    db.execute(delete(Storm).where(Storm.nhc_id == NHC_ID))
    db.commit()
    db.expunge_all()


def _upsert_storm(db: Session) -> Storm:
    existing = db.scalar(select(Storm).where(Storm.nhc_id == NHC_ID))
    if existing is not None:
        existing.name = "Irma"
        existing.storm_type = "Hurricane"
        existing.max_wind_kt = 155
        existing.min_pressure_mb = 924
        existing.status = "active"
        existing.genesis_date = date(2017, 8, 30)
        db.flush()
        return existing

    storm = Storm(
        nhc_id=NHC_ID,
        name="Irma",
        season_year=2017,
        storm_type="Hurricane",
        max_wind_kt=155,
        min_pressure_mb=924,
        genesis_date=date(2017, 8, 30),
        status="active",
    )
    db.add(storm)
    db.flush()
    return storm


def _upsert_observation(db: Session, storm: Storm) -> StormObservation:
    existing = db.scalar(
        select(StormObservation)
        .where(StormObservation.storm_id == storm.id)
        .where(StormObservation.observation_time == ADVISORY_ISSUED_AT)
    )
    if existing is not None:
        for field, value in OBSERVATION.items():
            setattr(existing, field, value)
        db.flush()
        return existing

    obs = StormObservation(
        storm_id=storm.id,
        observation_time=ADVISORY_ISSUED_AT,
        **OBSERVATION,
    )
    db.add(obs)
    db.flush()
    return obs


def _upsert_prior_observation(db: Session, storm: Storm) -> StormObservation:
    """Insert (or refresh) the 24h-prior observation.

    Panel 6's "What changed today" service compares the latest
    observation against the most recent observation at least 18h older.
    Without this prior row, the only observation in the dev DB is the
    Advisory-36 reading — and the service falls back to "newly tracked"
    instead of showing a real intensification delta.
    """
    existing = db.scalar(
        select(StormObservation)
        .where(StormObservation.storm_id == storm.id)
        .where(StormObservation.observation_time == PRIOR_OBSERVATION_TIME)
    )
    if existing is not None:
        for field, value in PRIOR_OBSERVATION.items():
            setattr(existing, field, value)
        db.flush()
        return existing

    obs = StormObservation(
        storm_id=storm.id,
        observation_time=PRIOR_OBSERVATION_TIME,
        **PRIOR_OBSERVATION,
    )
    db.add(obs)
    db.flush()
    return obs


def _upsert_forecast(db: Session, storm: Storm) -> Forecast:
    existing = db.scalar(
        select(Forecast)
        .where(Forecast.storm_id == storm.id)
        .where(Forecast.issued_at == ADVISORY_ISSUED_AT)
    )
    cone = _build_cone_geojson()
    points = _build_forecast_points()

    if existing is not None:
        existing.cone_geojson = cone
        existing.forecast_5day_points = points
        existing.raw_source_url = RAW_SOURCE_URL
        db.flush()
        return existing

    forecast = Forecast(
        storm_id=storm.id,
        issued_at=ADVISORY_ISSUED_AT,
        cone_geojson=cone,
        forecast_5day_points=points,
        raw_source_url=RAW_SOURCE_URL,
    )
    db.add(forecast)
    db.flush()
    return forecast


# ----- Orchestration ------------------------------------------------------


def seed(db: Session, *, clear: bool = False) -> dict[str, int]:
    """Insert (or refresh) Irma 2017 in the given session.

    Returns a small summary dict so the CLI can print it and tests (if
    we ever add them) have something to assert against. Does not commit;
    the caller decides.
    """
    if clear:
        _clear_existing(db)

    storm = _upsert_storm(db)
    obs = _upsert_observation(db, storm)
    _upsert_prior_observation(db, storm)
    forecast = _upsert_forecast(db, storm)
    return {
        "storm_id": storm.id,
        "observation_id": obs.id,
        "forecast_id": forecast.id,
        "forecast_points": len(forecast.forecast_5day_points or []),
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m rmn_dashboard.dev.seed_irma",
        description="Seed Hurricane Irma 2017 (Advisory 36) into the local SQLite DB.",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete any existing Irma rows before inserting (nukes observations+forecasts via cascade).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    _require_sqlite()

    db = SessionLocal()
    try:
        summary = seed(db, clear=args.clear)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    logger.info(
        "Seeded Irma 2017 — storm_id=%s obs_id=%s forecast_id=%s (%s forecast points).",
        summary["storm_id"],
        summary["observation_id"],
        summary["forecast_id"],
        summary["forecast_points"],
    )
    print("Done. Visit http://localhost:8000/ — Panel 1 should paint the Irma cone.")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
