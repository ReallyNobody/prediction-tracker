"""Historical analog finder — Panel 5.

When Panel 1 has an active forecast, we score each curated historical
analog by how close its landfall was to the centroid of the active
forecast cone, and return the closest 2-3. Off-season (no active
storms) we fall back to the most-recent N analogs by year — gives
readers a "recent reference points" view rather than an empty panel.

Why distance-based, not multi-dimensional similarity:

  * Distance is intuitive and explainable in a caption ("landfall
    near the same coast as Ian"). Composite scores over (distance +
    intensity + time-of-year) require a justification we can't fit
    in a panel caption.
  * The user is implicitly asking "what does this remind us of?"
    and the answer they want is geographic: "Florida storms look
    like other Florida storms."
  * Intensity / category similarity adds editorial value but as
    secondary tie-breakers, not the primary axis. Future iteration
    can layer that in once the basic geographic match is shipped.

Cone-centroid heuristic:

  * The "where the active storm will hit" approximation is the
    centroid of the cone polygon's bounding box. Coarse but adequate
    for "this cone is over Florida vs. Louisiana" comparisons.
  * Gulf and Atlantic storms differ by tens of degrees of longitude;
    centroid error of a degree or two is irrelevant at that scale.
"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy.orm import Session

from rmn_dashboard.data.analogs import HistoricalAnalog, load_analogs
from rmn_dashboard.services.forecasts import active_storm_forecasts

# Default number of analogs to return — three keeps the panel scannable.
_DEFAULT_LIMIT = 3

# Earth radius in kilometers, used by the haversine distance.
_EARTH_RADIUS_KM = 6371.0


def find_analogs(
    db: Session,
    *,
    limit: int = _DEFAULT_LIMIT,
    active_storms: list[dict] | None = None,
) -> dict[str, Any]:
    """Return the top N historical analogs.

    Two modes:

      * Active forecast — if any active storm has a parseable cone
        polygon, score each analog by haversine distance from its
        landfall to the cone centroid, return the closest N.
      * Off-season — return the N most-recent analogs by year.

    The mode is reflected in the response payload so the JS can render
    appropriate framing copy ("Most similar to today's forecast" vs.
    "Recent major Atlantic storms").

    ``active_storms`` is injectable for testing (lets us bypass the
    forecast service); production callers leave it None and the
    service queries it.
    """
    analogs_doc = load_analogs()
    analogs = list(analogs_doc.analogs)

    # Pull active forecasts if the caller didn't pre-fetch them.
    if active_storms is None:
        active_storms = active_storm_forecasts(db)

    cone_centroid = _first_cone_centroid(active_storms)
    if cone_centroid is None:
        return _offseason_response(analogs, limit=limit)
    return _active_response(analogs, cone_centroid, limit=limit)


# ----- Off-season path ----------------------------------------------------


def _offseason_response(analogs: list[HistoricalAnalog], *, limit: int) -> dict[str, Any]:
    """Most-recent N by year. Tie-break by name for stability."""
    sorted_analogs = sorted(analogs, key=lambda a: (-a.year, a.name))
    selected = sorted_analogs[:limit]
    return {
        "mode": "offseason",
        "framing": "Recent major Atlantic storms",
        "analogs": [_serialize(a) for a in selected],
    }


# ----- Active-storm path --------------------------------------------------


def _active_response(
    analogs: list[HistoricalAnalog],
    cone_centroid: tuple[float, float],
    *,
    limit: int,
) -> dict[str, Any]:
    """Closest N by haversine distance to the cone centroid."""
    centroid_lat, centroid_lon = cone_centroid
    scored = sorted(
        analogs,
        key=lambda a: _haversine_km(centroid_lat, centroid_lon, a.landfall_lat, a.landfall_lon),
    )
    selected = scored[:limit]
    return {
        "mode": "active",
        "framing": "Most similar past landfalls to today's forecast",
        "analogs": [
            _serialize(
                a,
                distance_km=round(
                    _haversine_km(centroid_lat, centroid_lon, a.landfall_lat, a.landfall_lon)
                ),
            )
            for a in selected
        ],
    }


# ----- Cone-centroid extraction ------------------------------------------


def _first_cone_centroid(active_storms: list[dict]) -> tuple[float, float] | None:
    """Return (lat, lon) of the bounding-box centroid of the first
    active storm's cone polygon, or None if no parseable cone is found.

    "First" is fine — when multiple storms are active we pick a single
    pivot rather than scoring against an averaged centroid (which
    could land in the middle of the ocean between two distant cones
    and produce nonsense rankings).
    """
    for entry in active_storms or []:
        forecast = entry.get("forecast") or {}
        cone = forecast.get("cone_geojson")
        if not cone:
            continue
        coords = cone.get("coordinates") or []
        if not coords:
            continue
        outer_ring = coords[0]
        if not outer_ring:
            continue
        lons = [pt[0] for pt in outer_ring]
        lats = [pt[1] for pt in outer_ring]
        # Bounding-box centroid; coarse but adequate for "Gulf vs. Atlantic"
        # discrimination at the scale our analogs live on.
        return (
            (min(lats) + max(lats)) / 2.0,
            (min(lons) + max(lons)) / 2.0,
        )
    return None


# ----- Haversine ---------------------------------------------------------


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two (lat, lon) points, in km.

    Spherical-earth approximation; ~0.5% error for short-to-medium
    distances. Good enough for "rank by closeness" — we're not landing
    a probe.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return _EARTH_RADIUS_KM * c


# ----- Serialization -----------------------------------------------------


def _serialize(analog: HistoricalAnalog, *, distance_km: int | None = None) -> dict[str, Any]:
    """Render an analog as a JSON-serializable dict.

    Distance is included only in the active-storm path; off-season
    "recent storms" omits it because the implied comparison is to
    today's date, not a geographic point.
    """
    payload: dict[str, Any] = {
        "name": analog.name,
        "year": analog.year,
        "peak_kt": analog.peak_kt,
        "saffir_simpson_at_landfall": analog.saffir_simpson_at_landfall,
        "landfall_state": analog.landfall_state,
        "insured_loss_usd_billions": analog.insured_loss_usd_billions,
        # Strip extra whitespace from YAML folded-block (`>`) so the JS
        # gets a single clean line to render.
        "narrative": " ".join(analog.narrative.split()),
    }
    if distance_km is not None:
        payload["distance_km"] = distance_km
    return payload
