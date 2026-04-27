"""Tests for ``services/historical_analogs.find_analogs``.

Lock down the contract Panel 5 will rely on:

  * Active-storm path: ranked by haversine distance from cone
    centroid to each analog's landfall (closest first).
  * Off-season path: most-recent N by year.
  * Mode + framing fields are present and correct.
  * Distance is included in active mode, absent in off-season.

Active-storm tests inject a synthetic ``active_storms`` payload so we
don't need to seed the full Storm/Forecast schema for ranking checks.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from rmn_dashboard.data.analogs import load_analogs
from rmn_dashboard.services.historical_analogs import find_analogs


@pytest.fixture(autouse=True)
def _clear_loader_cache() -> None:
    load_analogs.cache_clear()


def _cone_polygon(lat_min: float, lat_max: float, lon_min: float, lon_max: float) -> dict:
    """Synthetic /api/v1/forecasts/active payload entry with a square
    cone polygon at the requested bounding box."""
    coords = [
        [lon_min, lat_min],
        [lon_max, lat_min],
        [lon_max, lat_max],
        [lon_min, lat_max],
        [lon_min, lat_min],
    ]
    return {
        "storm": {"nhc_id": "AL992026", "name": "Test"},
        "current_position": None,
        "forecast": {
            "issued_at": "2026-09-15T12:00:00Z",
            "cone_geojson": {"type": "Polygon", "coordinates": [coords]},
            "forecast_5day_points": [],
        },
    }


# ----- Off-season path ----------------------------------------------------


def test_offseason_returns_most_recent_n(db_session: Session) -> None:
    """No active storms → most-recent 3 by year, mode='offseason'."""
    payload = find_analogs(db_session, active_storms=[])
    assert payload["mode"] == "offseason"
    assert payload["framing"] == "Recent major Atlantic storms"
    assert len(payload["analogs"]) == 3
    # Years descending.
    years = [a["year"] for a in payload["analogs"]]
    assert years == sorted(years, reverse=True)


def test_offseason_omits_distance_field(db_session: Session) -> None:
    """``distance_km`` is meaningful only when there's a cone to measure
    from. Off-season responses must not carry it.
    """
    payload = find_analogs(db_session, active_storms=[])
    for analog in payload["analogs"]:
        assert "distance_km" not in analog


def test_offseason_no_cone_in_active_storms_falls_back(db_session: Session) -> None:
    """An active-storms payload with no parseable cone (e.g., new
    storm before NHC has issued a cone) should fall back to the
    off-season path rather than crash.
    """
    no_cone = [{"storm": {"nhc_id": "AL01"}, "forecast": {"cone_geojson": None}}]
    payload = find_analogs(db_session, active_storms=no_cone)
    assert payload["mode"] == "offseason"


# ----- Active-storm path --------------------------------------------------


def test_active_florida_cone_ranks_florida_landfalls_first(
    db_session: Session,
) -> None:
    """A cone parked over peninsular FL should pull FL landfalls to
    the top of the ranking.
    """
    fl_cone = [_cone_polygon(lat_min=24.0, lat_max=29.0, lon_min=-83.0, lon_max=-80.0)]
    payload = find_analogs(db_session, active_storms=fl_cone, limit=3)
    assert payload["mode"] == "active"
    states = [a["landfall_state"] for a in payload["analogs"]]
    # Top three should all be FL given the bundled roster's FL bias.
    assert states.count("FL") == 3


def test_active_louisiana_cone_pulls_gulf_landfalls(db_session: Session) -> None:
    """A cone over Louisiana should not return Sandy 2012 (NJ) at the
    top — it should prefer LA / TX / MS analogs.
    """
    la_cone = [_cone_polygon(lat_min=28.0, lat_max=31.5, lon_min=-92.0, lon_max=-89.0)]
    payload = find_analogs(db_session, active_storms=la_cone, limit=3)
    states = {a["landfall_state"] for a in payload["analogs"]}
    # Sandy is NJ — should NOT be top-3 for a LA cone.
    names = {a["name"] for a in payload["analogs"]}
    assert "Hurricane Sandy" not in names
    # At least one of LA / TX / MS / FL should appear (Gulf-ish).
    assert states & {"LA", "TX", "MS", "FL"}


def test_active_response_includes_distance_km(db_session: Session) -> None:
    fl_cone = [_cone_polygon(lat_min=25.0, lat_max=29.0, lon_min=-83.0, lon_max=-80.0)]
    payload = find_analogs(db_session, active_storms=fl_cone, limit=3)
    for analog in payload["analogs"]:
        assert "distance_km" in analog
        assert isinstance(analog["distance_km"], int)
        assert analog["distance_km"] >= 0


def test_active_response_distance_increases_through_list(
    db_session: Session,
) -> None:
    """Closest first: distance_km on each successive entry should be
    >= the previous one.
    """
    fl_cone = [_cone_polygon(lat_min=25.0, lat_max=29.0, lon_min=-83.0, lon_max=-80.0)]
    payload = find_analogs(db_session, active_storms=fl_cone, limit=3)
    distances = [a["distance_km"] for a in payload["analogs"]]
    assert distances == sorted(distances)


# ----- Limit + payload shape ---------------------------------------------


def test_limit_kwarg_honored(db_session: Session) -> None:
    """Limit caps the count; default is 3 but callers can ask for more."""
    payload = find_analogs(db_session, active_storms=[], limit=5)
    assert len(payload["analogs"]) == 5

    payload = find_analogs(db_session, active_storms=[], limit=1)
    assert len(payload["analogs"]) == 1


def test_payload_shape(db_session: Session) -> None:
    """The fields the JS targets must be present on every analog."""
    payload = find_analogs(db_session, active_storms=[], limit=1)
    assert set(payload.keys()) == {"mode", "framing", "analogs"}
    assert isinstance(payload["analogs"], list)
    sample = payload["analogs"][0]
    expected = {
        "name",
        "year",
        "peak_kt",
        "saffir_simpson_at_landfall",
        "landfall_state",
        "insured_loss_usd_billions",
        "narrative",
    }
    assert expected.issubset(set(sample.keys()))


def test_narrative_has_no_yaml_folding_artifacts(db_session: Session) -> None:
    """YAML folded blocks (`>`) join multi-line strings with newlines.
    The serializer must collapse to single-line spaces so the JS gets
    a clean caption to render.
    """
    payload = find_analogs(db_session, active_storms=[], limit=1)
    sample = payload["analogs"][0]
    assert "\n" not in sample["narrative"]
    # And no double-spaces from naive str.replace either.
    assert "  " not in sample["narrative"]
