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

from typing import get_args

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from rmn_dashboard.data.universe import Sector
from rmn_dashboard.database import get_session
from rmn_dashboard.services.equity_quotes import latest_universe_quotes
from rmn_dashboard.services.forecasts import active_storm_forecasts

router = APIRouter(prefix="/api/v1", tags=["forecasts"])

_VALID_SECTORS = frozenset(get_args(Sector))


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


@router.get("/quotes/hurricane-universe")
def get_hurricane_universe_quotes(
    sectors: str | None = Query(
        default=None,
        description=(
            "Comma-separated list of sectors to include "
            "(insurer, reinsurer, homebuilder, utility). Omit for all."
        ),
    ),
    states: str | None = Query(
        default=None,
        description=(
            "Comma-separated 2-letter state codes for the cone-overlap "
            "highlight. Returns only tickers whose key_states intersect "
            "this set. Reinsurers (global books) are never returned by "
            "this filter — by editorial convention, not bug."
        ),
    ),
    db: Session = Depends(get_session),
) -> dict[str, list[dict]]:
    """Return one row per universe ticker, joined to its latest quote.

    Response shape::

        {
          "tickers": [
            {
              "ticker": "UVE",
              "name": "Universal Insurance Holdings",
              "sector": "insurer",
              "hurricane_relevance": "high",
              "key_states": ["FL"],
              "notes": "...",
              "quote": {
                  "last_price": 21.45,
                  "change_amount": 1.45,
                  "change_percent": 7.25,
                  "currency": "USD",
                  "volume": 1234567,
                  "market_cap": 651000000,
                  "as_of": "2026-04-24T17:00:00+00:00",
                  "source": "yfinance",
                  "prior_close": 20.0
              } | null
            },
            ...
          ]
        }

    Tickers with no quote yet still appear with ``"quote": null`` —
    the UI renders a "—" placeholder rather than dropping them entirely.
    This keeps the full curated universe visible during a scraper
    incident.
    """
    sector_list = _parse_sector_csv(sectors)
    state_list = _parse_state_csv(states)
    payload = latest_universe_quotes(db, sectors=sector_list, states=state_list)
    return {"tickers": payload}


def _parse_sector_csv(raw: str | None) -> list[str] | None:
    """Parse the ``?sectors=`` query into a list. Reject unknown values
    with a 400 — silently dropping a typo would let a UI bug ship a
    request that returns "everything" when the developer expected a
    narrow filter.
    """
    if raw is None:
        return None
    items = [s.strip().lower() for s in raw.split(",") if s.strip()]
    if not items:
        return None
    bad = [s for s in items if s not in _VALID_SECTORS]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown sector(s): {bad}. "
                f"Valid: {sorted(_VALID_SECTORS)}"
            ),
        )
    return items


def _parse_state_csv(raw: str | None) -> list[str] | None:
    """Parse the ``?states=`` query into a list. Validation of the
    individual codes happens inside ``tickers_for_states`` (which
    upper-cases) so we don't duplicate the postal-code list here.
    """
    if raw is None:
        return None
    items = [s.strip() for s in raw.split(",") if s.strip()]
    return items or None
