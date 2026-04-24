"""JSON API routes.

The HTML dashboard (``main.py``) is the primary UI, but Panel 1's cone
map is client-rendered via Leaflet — the server ships an empty <div> in
the template and the browser fetches GeoJSON over JSON. That fetch lands
here.

Keeping the JSON surface under a versioned ``/api/v1`` prefix means we
can evolve the payload (e.g., flip ``include_wsp`` to opt-out, add
``track_history``, add per-storm query) without breaking the dashboard
page; and if a downstream RMN tool or newsletter ever consumes these
endpoints, the versioning is already in place.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from rmn_dashboard.database import get_session
from rmn_dashboard.services.forecasts import active_storm_forecasts

router = APIRouter(prefix="/api/v1", tags=["forecasts"])


@router.get("/forecasts/active")
def get_active_forecasts(
    include_wsp: bool = Query(
        default=False,
        description=(
            "Include wind-probability GeoJSON in each forecast block. "
            "Default false because wsp_120hr can be multi-megabyte and "
            "Panel 1 (cone map) doesn't render it."
        ),
    ),
    db: Session = Depends(get_session),
) -> dict[str, list[dict]]:
    """Return one payload per active storm with its most recent forecast.

    Response shape::

        {
          "storms": [
            {
              "storm":            {"nhc_id": ..., "name": ..., ...},
              "current_position": {"latitude_deg": ..., ...} | null,
              "forecast": {
                "issued_at":            "...",
                "cone_geojson":         {...} | null,
                "forecast_5day_points": [...] | null,
                "wind_probability_geojson": {...} | null   # only if include_wsp=true
              }
            },
            ...
          ]
        }

    Empty ``storms`` list during the off-season (no active storms) — the
    JS client renders the empty state, not the server. Returning 200 + []
    rather than 404 keeps the UI code branch-free.
    """
    return {"storms": active_storm_forecasts(db, include_wsp=include_wsp)}
