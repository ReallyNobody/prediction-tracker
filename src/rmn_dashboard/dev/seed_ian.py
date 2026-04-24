"""Seed Hurricane Ian 2022 (Advisory 19) into the local dev DB.

Purpose: give dashboard developers a **landfall-probability Panel 4**
view during the off-season, without pointing at the live NHC feed.
Complements ``seed_irma.py`` — Irma exercises the cone map (Panel 1);
Ian exercises the WSP choropleth (Panel 4) because its 34/50/64 kt
wind-speed-probability contours over SW Florida were the canonical
"red on top of Fort Myers 30 hours out" product, and a useful stress
test of our choropleth rendering.

Why Ian 2022:
  * Advisory ~19 (2022-09-27 09:00Z), roughly 30 hours before
    landfall at Cayo Costa, FL, had WSP contours with ≥90% probability
    of hurricane-force winds over Charlotte Harbor. That's as
    journalistically vivid as landfall forecasts get.
  * Ian eventually peaked at 135 kt (briefly Cat 5 over the Gulf) and
    did a secondary landfall at SC — the 5-day cone extends from
    western Cuba all the way up the Atlantic coast, so the cone +
    WSP combination exercises most of the basin.

Running:

    python -m rmn_dashboard.dev.seed_ian
    python -m rmn_dashboard.dev.seed_ian --clear   # drop & re-seed

After running, visit http://localhost:8000/. Panel 4 should paint the
WSP choropleth over the Gulf + Florida peninsula with the default 34 kt
threshold; flipping the dropdown to 50 or 64 kt should show tighter
contours clustered on SW Florida.

Shape conventions mirror production:

  * ``Forecast.cone_geojson``               — bare GeoJSON Polygon.
  * ``Forecast.forecast_5day_points``       — list of GeoJSON Features
                                              with Point geometries and
                                              DBF-style properties.
  * ``Forecast.wind_probability_geojson``   — GeoJSON FeatureCollection;
                                              each Feature is a Polygon
                                              with properties
                                              ``{PWIND: <0-100>,
                                              threshold_kt: 34|50|64}``.
                                              Matches the output of
                                              ``scrapers.nhc_shapefiles
                                              .parse_wind_probability_zip``
                                              so the seed is
                                              interchangeable with the
                                              real parser from the UI's
                                              perspective.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from rmn_dashboard.config import settings
from rmn_dashboard.database import SessionLocal, normalize_database_url
from rmn_dashboard.models import Forecast, Storm, StormObservation

logger = logging.getLogger(__name__)

# ----- Canonical Ian 2022 record ------------------------------------------

NHC_ID = "AL092022"
ADVISORY_ISSUED_AT = datetime(2022, 9, 27, 9, 0, tzinfo=UTC)

# Advisory 19 observation (the "current position" marker on the map).
# At 09:00Z on 2022-09-27 Ian was over western Cuba / the Florida
# Straits, already a major (Cat 3) hurricane and tracking NNE toward
# the Florida Gulf coast.
OBSERVATION = {
    "classification": "HU",
    "intensity_kt": 105,
    "pressure_mb": 952,
    "latitude_deg": 22.5,
    "longitude_deg": -82.8,
    "movement_dir_deg": 20,  # NNE
    "movement_speed_mph": 10,
}

# 5-day forecast track. The +24–36h rows bracket FL landfall at Cayo
# Costa (2022-09-28 ~19:05Z) — the track points just before and just
# after are the ones that will read as "the landfall" on the cone map.
FORECAST_POINTS = [
    # (valid_time, lat, lon, max_wind_kt, stage_code, human_label)
    (datetime(2022, 9, 27, 18, 0, tzinfo=UTC), 23.5, -82.9, 115, "HU", "27/18Z TUE"),
    (datetime(2022, 9, 28, 6, 0, tzinfo=UTC), 25.2, -82.8, 125, "HU", "28/06Z WED"),
    (datetime(2022, 9, 28, 18, 0, tzinfo=UTC), 27.0, -82.2, 120, "HU", "28/18Z WED"),
    (datetime(2022, 9, 29, 6, 0, tzinfo=UTC), 28.2, -81.4, 75, "HU", "29/06Z THU"),
    (datetime(2022, 9, 30, 6, 0, tzinfo=UTC), 30.5, -79.2, 60, "TS", "30/06Z FRI"),
    (datetime(2022, 10, 1, 6, 0, tzinfo=UTC), 32.8, -78.1, 55, "TS", "01/06Z SAT"),
    (datetime(2022, 10, 2, 6, 0, tzinfo=UTC), 34.5, -77.0, 25, "TD", "02/06Z SUN"),
]

# 5-day cone polygon. Traces an envelope from current position (western
# Cuba / FL Straits) up through SW Florida, central FL, out over the
# Atlantic, and dissipating near the NC/VA coast. Not a verbatim
# reproduction of Advisory 19's published cone (NHC's fstadv archive
# text doesn't publish the vertex table) but close enough in shape for
# the dashboard to render a realistic picture.
CONE_POLYGON_COORDS = [
    # Start near current position, trace UP the eastern edge, across
    # the top, back DOWN the western edge. GeoJSON uses [lon, lat].
    [-81.5, 22.5],  # start, east side
    [-81.0, 23.5],
    [-80.8, 25.0],
    [-80.5, 27.0],
    [-79.5, 29.0],
    [-78.5, 31.0],
    [-76.8, 33.5],
    [-75.0, 35.5],  # top-east, off NC coast
    # Top cap
    [-78.5, 35.5],  # top-west, over NC
    # West side coming back down
    [-79.5, 33.0],
    [-80.5, 31.0],
    [-82.0, 29.0],
    [-83.5, 27.0],
    [-84.0, 25.0],
    [-84.2, 23.5],
    [-83.8, 22.5],
    [-81.5, 22.5],  # close ring
]

# Wind-speed probability (WSP) configuration.
#
# Production NHC ``wsp_120hr`` ships one shapefile per threshold (34 /
# 50 / 64 kt), and within each shapefile multiple polygons represent
# the ≥N% probability contour rings. We synthesize the same structure
# procedurally — concentric envelopes around the landfall point, each
# tagged with a PWIND band and a threshold_kt value — so the payload
# stored in ``Forecast.wind_probability_geojson`` is shape-compatible
# with what ``parse_wind_probability_zip`` produces.
#
# Each entry is (threshold_kt, [(pwind_band, radius_lat_deg,
# radius_lon_deg, north_elongation_deg), ...]).
#
# Shapes elongate northward because the storm is tracking NNE — that
# asymmetry matches real NHC WSP products. Elongation > radius_lat
# stretches the northern lobe; equal values produce a circular ring.

LANDFALL_CENTER_LAT = 26.6  # Cayo Costa, FL
LANDFALL_CENTER_LON = -82.3

WSP_BAND_CONFIG: list[tuple[int, list[tuple[int, float, float, float]]]] = [
    (
        34,
        [
            # (pwind %, r_lat, r_lon, north_elongation)
            (5, 6.2, 6.8, 4.8),
            (20, 4.2, 4.6, 3.2),
            (50, 2.4, 2.8, 2.1),
            (80, 1.1, 1.4, 1.3),
        ],
    ),
    (
        50,
        [
            (10, 3.8, 4.2, 2.8),
            (30, 2.2, 2.5, 1.9),
            (60, 1.1, 1.3, 1.1),
            (80, 0.55, 0.75, 0.65),
        ],
    ),
    (
        64,
        [
            (10, 2.3, 2.6, 1.9),
            (30, 1.3, 1.5, 1.3),
            (60, 0.65, 0.85, 0.75),
            (80, 0.35, 0.48, 0.42),
        ],
    ),
]

_WSP_POLYGON_VERTICES = 16  # enough that the ring reads smoothly at zoom 5

RAW_SOURCE_URL = (
    "https://www.nhc.noaa.gov/archive/2022/al09/al092022.fstadv.019.shtml?"
    "via=rmn-dashboard-dev-seed"
)


# ----- Guardrails ---------------------------------------------------------


def _require_sqlite() -> None:
    """Refuse to run against a non-SQLite DB.

    Same contract as ``seed_irma._require_sqlite`` — the seed is for
    local dev only; running it against Render's Postgres (or any other
    non-SQLite URL) would muddy real analysis with synthetic data.
    """
    url = normalize_database_url(settings.database_url)
    if not url.startswith("sqlite"):
        raise SystemExit(
            "seed_ian refuses to run against a non-SQLite database "
            f"(DATABASE_URL={settings.database_url!r}). "
            "This seed is for local dev only."
        )


# ----- Payload builders ---------------------------------------------------


def _build_cone_geojson() -> dict:
    """Return the bare GeoJSON Polygon geometry used in production."""
    return {
        "type": "Polygon",
        "coordinates": [CONE_POLYGON_COORDS],
    }


def _build_forecast_points() -> list[dict]:
    """Return the 5-day forecast points as GeoJSON Point Features.

    DBF-field naming mirrors what ``nhc_shapefiles`` produces from a
    real NHC ``_5day_pts.shp`` payload so the seed is interchangeable
    with the real parser. forecast_map.js reads FLDATELBL / MAXWIND /
    TCDVLP for the popup.
    """
    features: list[dict] = []
    for valid_time, lat, lon, max_wind, stage, label in FORECAST_POINTS:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "ADVISNUM": "019",
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


def _wsp_ring(
    center_lat: float,
    center_lon: float,
    radius_lat: float,
    radius_lon: float,
    north_elongation: float,
    n_vertices: int = _WSP_POLYGON_VERTICES,
) -> list[list[float]]:
    """Build one closed polygon ring centered at (center_lat, center_lon).

    ``radius_lat`` / ``radius_lon`` give the north-south / east-west
    semi-axes. ``north_elongation`` stretches the ring northward: in
    the top half (``sin(theta) > 0``) we smoothly interpolate between
    ``radius_lat`` and ``north_elongation`` using ``sin²(theta)`` so
    the east and west flanks stay tight while the northern nose
    extends along the storm track.

    Returns coordinates in GeoJSON order ([lon, lat]), with the first
    vertex repeated at the end to close the ring.
    """
    ring: list[list[float]] = []
    for i in range(n_vertices):
        theta = 2 * math.pi * i / n_vertices  # 0 = east, π/2 = north
        north_component = math.sin(theta)
        # Smooth blend: on the north half, lat-radius grows toward
        # north_elongation; on the south half, stays at radius_lat.
        effective_lat_radius = radius_lat
        if north_component > 0:
            effective_lat_radius += (north_elongation - radius_lat) * north_component**2
        lat = center_lat + effective_lat_radius * north_component
        lon = center_lon + radius_lon * math.cos(theta)
        ring.append([round(lon, 4), round(lat, 4)])
    ring.append(ring[0])  # close the ring
    return ring


def _build_wsp_geojson() -> dict:
    """Return a FeatureCollection of WSP polygons for Panel 4.

    One Feature per (threshold, pwind band) pair — 3 thresholds × 4
    bands = 12 polygons, all centered on the Ian landfall point and
    stretched northward along the storm track. Each Feature carries
    ``PWIND`` (the band's lower-bound probability) and
    ``threshold_kt`` so the frontend can filter by threshold and color
    by band — exactly as it does with production data.
    """
    features: list[dict] = []
    for threshold_kt, bands in WSP_BAND_CONFIG:
        for pwind, r_lat, r_lon, north_elong in bands:
            ring = _wsp_ring(
                LANDFALL_CENTER_LAT,
                LANDFALL_CENTER_LON,
                r_lat,
                r_lon,
                north_elong,
            )
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                    "properties": {
                        "PWIND": pwind,
                        "threshold_kt": threshold_kt,
                    },
                }
            )
    return {"type": "FeatureCollection", "features": features}


# ----- DB writes ----------------------------------------------------------


def _clear_existing(db: Session) -> None:
    """Drop Ian's Storm row (cascade-deletes observations + forecasts
    via ``ondelete=CASCADE`` on their FKs).
    """
    db.execute(delete(Storm).where(Storm.nhc_id == NHC_ID))
    db.commit()


def _upsert_storm(db: Session) -> Storm:
    existing = db.scalar(select(Storm).where(Storm.nhc_id == NHC_ID))
    if existing is not None:
        existing.name = "Ian"
        existing.storm_type = "Hurricane"
        existing.max_wind_kt = 135  # Ian peaked at Cat 5 over the Gulf
        existing.min_pressure_mb = 937
        existing.status = "active"
        existing.genesis_date = date(2022, 9, 23)
        db.flush()
        return existing

    storm = Storm(
        nhc_id=NHC_ID,
        name="Ian",
        season_year=2022,
        storm_type="Hurricane",
        max_wind_kt=135,
        min_pressure_mb=937,
        genesis_date=date(2022, 9, 23),
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


def _upsert_forecast(db: Session, storm: Storm) -> Forecast:
    existing = db.scalar(
        select(Forecast)
        .where(Forecast.storm_id == storm.id)
        .where(Forecast.issued_at == ADVISORY_ISSUED_AT)
    )
    cone = _build_cone_geojson()
    points = _build_forecast_points()
    wsp = _build_wsp_geojson()

    if existing is not None:
        existing.cone_geojson = cone
        existing.forecast_5day_points = points
        existing.wind_probability_geojson = wsp
        existing.raw_source_url = RAW_SOURCE_URL
        db.flush()
        return existing

    forecast = Forecast(
        storm_id=storm.id,
        issued_at=ADVISORY_ISSUED_AT,
        cone_geojson=cone,
        forecast_5day_points=points,
        wind_probability_geojson=wsp,
        raw_source_url=RAW_SOURCE_URL,
    )
    db.add(forecast)
    db.flush()
    return forecast


# ----- Orchestration ------------------------------------------------------


def seed(db: Session, *, clear: bool = False) -> dict[str, int]:
    """Insert (or refresh) Ian 2022 in the given session.

    Returns a small summary dict so the CLI can print it and tests
    have something to assert against. Does not commit; the caller
    decides.
    """
    if clear:
        _clear_existing(db)

    storm = _upsert_storm(db)
    obs = _upsert_observation(db, storm)
    forecast = _upsert_forecast(db, storm)
    wsp = forecast.wind_probability_geojson or {}
    wsp_features = wsp.get("features") if isinstance(wsp, dict) else []
    return {
        "storm_id": storm.id,
        "observation_id": obs.id,
        "forecast_id": forecast.id,
        "forecast_points": len(forecast.forecast_5day_points or []),
        "wsp_features": len(wsp_features or []),
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m rmn_dashboard.dev.seed_ian",
        description="Seed Hurricane Ian 2022 (Advisory 19) into the local SQLite DB.",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help=(
            "Delete any existing Ian rows before inserting "
            "(nukes observations + forecasts via cascade)."
        ),
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
        "Seeded Ian 2022 — storm_id=%s obs_id=%s forecast_id=%s "
        "(%s forecast points, %s WSP features).",
        summary["storm_id"],
        summary["observation_id"],
        summary["forecast_id"],
        summary["forecast_points"],
        summary["wsp_features"],
    )
    print(
        "Done. Visit http://localhost:8000/ — Panel 4 should paint the "
        "Ian WSP choropleth; flip the threshold dropdown to compare 34 / "
        "50 / 64 kt bands."
    )
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
