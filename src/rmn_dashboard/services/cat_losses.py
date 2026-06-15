"""Cat-loss-estimates service — Panel 7.

Reads the bundled ``cat_loss_estimates.yaml`` (via ``data/cat_losses``)
and shapes it into panel-ready JSON. Same authorship pattern as
``services/historical_analogs.py``: pure read-from-YAML, no DB writes,
two modes (active vs. off-season) reflected in the response payload.

Two modes:

  * **Active** — when an active storm in the current season matches
    a curated event in the YAML by name, return that event's estimates
    as they come in. The JS renders an "incoming estimates" framing.
  * **Off-season** — return the most-recent prior event by year. The
    JS renders a "most-recent event with completed estimates" framing.

The match between an active storm (e.g. ``Storm.name == "Helene"``)
and a curated event (``event_name == "Hurricane Helene"``) is done by
suffix-matching on the storm name — case-insensitive — so editorial
doesn't need to coordinate string conventions with the NHC scraper.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from rmn_dashboard.data.cat_losses import (
    CatLossEstimate,
    CatLossEstimates,
    CatLossEvent,
    load_cat_losses,
)
from rmn_dashboard.services.forecasts import active_storm_forecasts


def recent_event_payload(
    db: Session,
    *,
    estimates: CatLossEstimates | None = None,
    active_storms: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the panel-ready payload for Panel 7.

    ``estimates`` and ``active_storms`` are injectable for testing —
    production callers leave both None and the service queries them.

    Response shape::

        {
          "mode": "active" | "offseason",
          "framing": str,
          "event": {
            "event_name": "Hurricane Helene",
            "year": 2024,
            "estimates": [
              {
                "modeler": "Karen Clark & Company",
                "low_usd_billions": 6.4,
                "high_usd_billions": 6.4,
                "midpoint_usd_billions": 6.4,
                "is_point_estimate": true,
                "issued_at": "2024-10-02",
                "source_url": "https://...",
                "refinement_note": "Point estimate ..."
              },
              ...
            ],
            "trajectory": [ ... ],
            "consensus_midpoint_usd_billions": 11.7,
            "dispersion_usd_billions": 4.5,
            "modeler_count": 3
          }
        } | { "mode": ..., "framing": ..., "event": None }
    """
    losses_doc = estimates if estimates is not None else load_cat_losses()
    events = list(losses_doc.events)
    if not events:
        return _empty_response()

    # Active path: if any active storm matches a curated event by name,
    # surface that event's incoming estimates.
    if active_storms is None:
        active_storms = active_storm_forecasts(db)
    active_event = _match_active_event(events, active_storms)
    if active_event is not None:
        return _active_response(active_event)

    return _offseason_response(events)


# ----- Off-season path ----------------------------------------------------


def _offseason_response(events: list[CatLossEvent]) -> dict[str, Any]:
    """Most-recent event by year; tie-break by event_name for stability."""
    sorted_events = sorted(events, key=lambda e: (-e.year, e.event_name))
    selected = sorted_events[0]
    return {
        "mode": "offseason",
        "framing": "Most recent event with modeled losses",
        "event": _serialize_event(selected),
    }


# ----- Active path -------------------------------------------------------


def _active_response(event: CatLossEvent) -> dict[str, Any]:
    return {
        "mode": "active",
        "framing": "Modelers are publishing estimates for this event",
        "event": _serialize_event(event),
    }


def _match_active_event(
    events: list[CatLossEvent], active_storms: list[dict[str, Any]]
) -> CatLossEvent | None:
    """Return the curated event matching an active storm by name.

    Match rule: case-insensitive suffix match. ``Storm.name == "Helene"``
    matches ``event_name == "Hurricane Helene"`` and ``event_name ==
    "Tropical Storm Helene"`` and similar. Returns None if no active
    storm matches a curated event — in which case the caller falls
    through to the off-season path.

    When multiple active storms each match curated events (rare, but
    possible during a peak-season cluster), we return the highest-year
    match. Two same-year matches tie-break by event_name for stability.
    """
    if not events or not active_storms:
        return None

    active_names_lower: set[str] = set()
    for storm in active_storms:
        storm_block = storm.get("storm") if isinstance(storm, dict) else None
        name = (storm_block or {}).get("name") if isinstance(storm_block, dict) else None
        if isinstance(name, str) and name.strip():
            active_names_lower.add(name.strip().lower())

    if not active_names_lower:
        return None

    matches: list[CatLossEvent] = []
    for event in events:
        event_name_lower = event.event_name.lower()
        for storm_name_lower in active_names_lower:
            # Suffix match: "Hurricane Helene" ends with " helene"
            # (with a leading space to avoid matching "Hurricane MyHelene").
            if event_name_lower.endswith(" " + storm_name_lower):
                matches.append(event)
                break

    if not matches:
        return None
    matches.sort(key=lambda e: (-e.year, e.event_name))
    return matches[0]


# ----- Serialization ----------------------------------------------------


def _serialize_event(event: CatLossEvent) -> dict[str, Any]:
    latest = event.latest_per_modeler()
    return {
        "event_name": event.event_name,
        "year": event.year,
        "estimates": [_serialize_estimate(e) for e in latest],
        "trajectory": [_serialize_estimate(e) for e in event.estimates],
        "consensus_midpoint_usd_billions": round(event.consensus_midpoint_usd_billions, 2),
        "dispersion_usd_billions": round(event.dispersion_usd_billions, 2),
        "modeler_count": len(latest),
    }


def _serialize_estimate(est: CatLossEstimate) -> dict[str, Any]:
    return {
        "modeler": est.modeler,
        "low_usd_billions": est.low_usd_billions,
        "high_usd_billions": est.high_usd_billions,
        "midpoint_usd_billions": round(est.midpoint_usd_billions, 2),
        "is_point_estimate": est.is_point_estimate,
        "issued_at": est.issued_at.isoformat(),
        "source_url": est.source_url,
        "refinement_note": est.refinement_note,
    }


def _empty_response() -> dict[str, Any]:
    """Returned when the YAML happens to be empty — defensive only.

    Editorial seed always carries at least one event, but if a fresh
    YAML is being staged without entries, the panel renders an empty
    state rather than 500-ing.
    """
    return {
        "mode": "offseason",
        "framing": "No modeled-loss estimates available",
        "event": None,
    }
