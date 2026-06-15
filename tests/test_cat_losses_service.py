"""Tests for the cat-losses service (``services/cat_losses.py``).

Mirrors the structure of ``test_historical_analogs_service.py``: lock
down the response shape, the off-season vs. active-mode branching, and
the active-storm name-matching rule.

Both ``estimates`` and ``active_storms`` are injectable on
``recent_event_payload`` so we don't need DB seeding for these tests —
they're pure transformation logic over an in-memory YAML doc.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from rmn_dashboard.data.cat_losses import CatLossEstimates
from rmn_dashboard.services.cat_losses import recent_event_payload


def _make_doc(events: list[dict[str, Any]]) -> CatLossEstimates:
    """Build an in-memory CatLossEstimates from a plain dict, bypassing
    the YAML loader so tests can construct edge cases concisely."""
    return CatLossEstimates.model_validate(
        {
            "version": 1,
            "last_reviewed": date(2026, 6, 12),
            "events": events,
        }
    )


def _est(modeler: str, low: float, high: float, issued: date) -> dict[str, Any]:
    return {
        "modeler": modeler,
        "low_usd_billions": low,
        "high_usd_billions": high,
        "issued_at": issued,
    }


# ----- Off-season path ---------------------------------------------------


def test_offseason_returns_most_recent_event_by_year(db_session: Session) -> None:
    doc = _make_doc(
        [
            {
                "event_name": "Hurricane Older",
                "year": 2020,
                "estimates": [_est("Verisk", 10.0, 15.0, date(2020, 9, 5))],
            },
            {
                "event_name": "Hurricane Newer",
                "year": 2024,
                "estimates": [_est("Verisk", 20.0, 30.0, date(2024, 10, 5))],
            },
            {
                "event_name": "Hurricane Middle",
                "year": 2022,
                "estimates": [_est("Verisk", 15.0, 25.0, date(2022, 9, 30))],
            },
        ]
    )
    payload = recent_event_payload(db_session, estimates=doc, active_storms=[])
    assert payload["mode"] == "offseason"
    assert payload["event"]["event_name"] == "Hurricane Newer"
    assert payload["event"]["year"] == 2024


def test_offseason_tiebreaks_same_year_events_by_name(db_session: Session) -> None:
    """Two events in the same year sort alphabetically by event_name
    for stable panel rendering."""
    doc = _make_doc(
        [
            {
                "event_name": "Hurricane Milton",
                "year": 2024,
                "estimates": [_est("Verisk", 20.0, 30.0, date(2024, 10, 15))],
            },
            {
                "event_name": "Hurricane Helene",
                "year": 2024,
                "estimates": [_est("Verisk", 10.0, 16.0, date(2024, 10, 7))],
            },
        ]
    )
    payload = recent_event_payload(db_session, estimates=doc, active_storms=[])
    assert payload["mode"] == "offseason"
    # Alphabetical: "Hurricane Helene" < "Hurricane Milton"
    assert payload["event"]["event_name"] == "Hurricane Helene"


# ----- Response shape ----------------------------------------------------


def test_response_shape_carries_panel_ready_fields(db_session: Session) -> None:
    doc = _make_doc(
        [
            {
                "event_name": "Hurricane Test",
                "year": 2024,
                "estimates": [
                    _est("Verisk", 10.0, 20.0, date(2024, 10, 1)),
                    _est("Verisk", 12.0, 16.0, date(2024, 10, 15)),  # later
                    _est("Karen Clark & Company", 18.0, 18.0, date(2024, 10, 2)),
                ],
            }
        ]
    )
    payload = recent_event_payload(db_session, estimates=doc, active_storms=[])
    assert set(payload.keys()) == {"mode", "framing", "event"}
    event = payload["event"]
    assert event["event_name"] == "Hurricane Test"
    assert event["year"] == 2024
    assert event["modeler_count"] == 2  # Verisk + KCC, latest-per-modeler
    # Verisk latest midpoint = 14.0; KCC = 18.0; consensus = 16.0
    assert event["consensus_midpoint_usd_billions"] == 16.0
    # |18.0 - 14.0| = 4.0
    assert event["dispersion_usd_billions"] == 4.0
    # estimates is latest-per-modeler (2 rows), trajectory is full (3 rows)
    assert len(event["estimates"]) == 2
    assert len(event["trajectory"]) == 3
    # Per-estimate shape:
    sample = event["estimates"][0]
    expected_estimate_keys = {
        "modeler",
        "low_usd_billions",
        "high_usd_billions",
        "midpoint_usd_billions",
        "is_point_estimate",
        "issued_at",
        "source_url",
        "refinement_note",
    }
    assert set(sample.keys()) == expected_estimate_keys


def test_iso_date_in_serialized_estimate(db_session: Session) -> None:
    doc = _make_doc(
        [
            {
                "event_name": "Hurricane Test",
                "year": 2024,
                "estimates": [_est("Verisk", 10.0, 15.0, date(2024, 10, 1))],
            }
        ]
    )
    payload = recent_event_payload(db_session, estimates=doc, active_storms=[])
    assert payload["event"]["estimates"][0]["issued_at"] == "2024-10-01"


# ----- Active path -------------------------------------------------------


def _active_storm(name: str) -> dict[str, Any]:
    """Minimal shape of an entry in the active-storm forecasts list —
    matches what services/forecasts.active_storm_forecasts() emits."""
    return {"storm": {"nhc_id": "AL012026", "name": name, "status": "active"}}


def test_active_mode_picks_event_matching_storm_name(db_session: Session) -> None:
    doc = _make_doc(
        [
            {
                "event_name": "Hurricane Older",
                "year": 2020,
                "estimates": [_est("Verisk", 10.0, 15.0, date(2020, 9, 5))],
            },
            {
                "event_name": "Hurricane Helene",
                "year": 2024,
                "estimates": [_est("Verisk", 10.0, 16.0, date(2024, 10, 7))],
            },
        ]
    )
    payload = recent_event_payload(
        db_session,
        estimates=doc,
        active_storms=[_active_storm("Helene")],
    )
    assert payload["mode"] == "active"
    assert payload["event"]["event_name"] == "Hurricane Helene"


def test_active_match_is_case_insensitive(db_session: Session) -> None:
    doc = _make_doc(
        [
            {
                "event_name": "Hurricane Helene",
                "year": 2024,
                "estimates": [_est("Verisk", 10.0, 16.0, date(2024, 10, 7))],
            }
        ]
    )
    payload = recent_event_payload(
        db_session,
        estimates=doc,
        active_storms=[_active_storm("HELENE")],
    )
    assert payload["mode"] == "active"


def test_active_match_uses_space_boundary_not_naive_endswith(db_session: Session) -> None:
    """Suffix match requires a leading space so 'MyHelene' doesn't
    spuriously match 'Hurricane Helene'."""
    doc = _make_doc(
        [
            {
                "event_name": "Hurricane Helene",
                "year": 2024,
                "estimates": [_est("Verisk", 10.0, 16.0, date(2024, 10, 7))],
            }
        ]
    )
    payload = recent_event_payload(
        db_session,
        estimates=doc,
        active_storms=[_active_storm("MyHelene")],
    )
    # Not a match — falls back to off-season
    assert payload["mode"] == "offseason"


def test_active_mode_falls_through_when_no_storm_matches(db_session: Session) -> None:
    """An active storm with no curated event should not force active
    mode — fall through to off-season."""
    doc = _make_doc(
        [
            {
                "event_name": "Hurricane Helene",
                "year": 2024,
                "estimates": [_est("Verisk", 10.0, 16.0, date(2024, 10, 7))],
            },
            {
                "event_name": "Hurricane Older",
                "year": 2020,
                "estimates": [_est("Verisk", 10.0, 15.0, date(2020, 9, 5))],
            },
        ]
    )
    payload = recent_event_payload(
        db_session,
        estimates=doc,
        active_storms=[_active_storm("Unknown")],
    )
    assert payload["mode"] == "offseason"
    assert payload["event"]["event_name"] == "Hurricane Helene"  # most recent prior


def test_active_picks_highest_year_when_multiple_match(db_session: Session) -> None:
    """Two active storms each matching different curated events: return
    the higher-year match for stability."""
    doc = _make_doc(
        [
            {
                "event_name": "Hurricane Helene",
                "year": 2024,
                "estimates": [_est("Verisk", 10.0, 16.0, date(2024, 10, 7))],
            },
            {
                "event_name": "Hurricane Older",
                "year": 2020,
                "estimates": [_est("Verisk", 5.0, 8.0, date(2020, 9, 5))],
            },
        ]
    )
    payload = recent_event_payload(
        db_session,
        estimates=doc,
        active_storms=[_active_storm("Helene"), _active_storm("Older")],
    )
    assert payload["mode"] == "active"
    assert payload["event"]["event_name"] == "Hurricane Helene"


# ----- Defensive empty-doc -----------------------------------------------


def test_empty_doc_renders_empty_state(db_session: Session) -> None:
    doc = _make_doc([])
    payload = recent_event_payload(db_session, estimates=doc, active_storms=[])
    assert payload["mode"] == "offseason"
    assert payload["event"] is None


# ----- Bundled YAML smoke ------------------------------------------------


def test_bundled_yaml_renders_through_service(db_session: Session) -> None:
    """End-to-end smoke: load the actual bundled YAML, run it through
    the service with no active storms, assert we get a shaped payload.

    This catches: missing API/schema drift between the data loader and
    the service serializer (the editorial seed gains a new event but
    the serializer wasn't updated)."""
    payload = recent_event_payload(db_session, active_storms=[])
    assert payload["mode"] == "offseason"
    assert payload["event"] is not None
    event = payload["event"]
    assert "consensus_midpoint_usd_billions" in event
    assert event["modeler_count"] >= 1
    # Seed launched with Ian 2022, Helene 2024, Milton 2024 — most
    # recent by year is one of the 2024 events.
    assert event["year"] == 2024
