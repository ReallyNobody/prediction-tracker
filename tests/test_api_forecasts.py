"""Unit tests for GET /api/v1/forecasts/active.

Covers the service layer (``active_storm_forecasts``) and the FastAPI
endpoint that wraps it. All DB-backed; no HTTP mocking needed because
the endpoint talks only to the in-memory SQLite fixture.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from rmn_dashboard.models import Forecast, Storm, StormObservation
from rmn_dashboard.services.forecasts import active_storm_forecasts

# ----- Fixtures ------------------------------------------------------------


def _make_storm(
    db: Session,
    *,
    nhc_id: str,
    name: str,
    status: str = "active",
    season_year: int = 2017,
) -> Storm:
    storm = Storm(
        nhc_id=nhc_id,
        name=name,
        season_year=season_year,
        storm_type="Hurricane",
        status=status,
        max_wind_kt=145,
    )
    db.add(storm)
    db.flush()
    return storm


def _make_observation(
    db: Session,
    storm: Storm,
    *,
    observation_time: datetime,
    intensity_kt: int = 125,
    latitude_deg: float = 22.9,
    longitude_deg: float = -79.9,
) -> StormObservation:
    obs = StormObservation(
        storm_id=storm.id,
        classification="HU",
        intensity_kt=intensity_kt,
        pressure_mb=930,
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        movement_dir_deg=280,
        movement_speed_mph=8,
        observation_time=observation_time,
        advisory_urls={},
    )
    db.add(obs)
    db.flush()
    return obs


_DEFAULT_CONE = {
    "type": "Polygon",
    "coordinates": [[[-80.0, 22.0], [-78.0, 22.0], [-78.0, 25.0], [-80.0, 25.0], [-80.0, 22.0]]],
}
_DEFAULT_POINTS = [
    {"lat": 22.9, "lng": -79.9, "valid_time": "2017-09-09T18:00:00Z"},
    {"lat": 24.0, "lng": -80.5, "valid_time": "2017-09-10T00:00:00Z"},
]
_DEFAULT_WSP = {
    "type": "FeatureCollection",
    "features": [{"type": "Feature", "properties": {"threshold_kt": 34}, "geometry": None}],
}


def _make_forecast(
    db: Session,
    storm: Storm,
    *,
    issued_at: datetime,
    cone: dict | None = None,
    points: list | None = None,
    wsp: dict | None = None,
) -> Forecast:
    fc = Forecast(
        storm_id=storm.id,
        issued_at=issued_at,
        cone_geojson=cone if cone is not None else _DEFAULT_CONE,
        forecast_5day_points=points if points is not None else _DEFAULT_POINTS,
        wind_probability_geojson=wsp,
        raw_source_url="https://example.test/track.zip",
    )
    db.add(fc)
    db.flush()
    return fc


# ----- Service layer -------------------------------------------------------


def test_active_storm_forecasts_returns_empty_when_no_active_storms(
    db_session: Session,
) -> None:
    """Off-season steady state: zero active storms → zero payloads."""
    assert active_storm_forecasts(db_session) == []


def test_active_storm_forecasts_skips_dissipated_storms(db_session: Session) -> None:
    """A dissipated storm with a full observation+forecast history must
    not appear in the active-forecasts endpoint — the panel only cares
    about what's in the water *right now*."""
    storm = _make_storm(db_session, nhc_id="AL012017", name="Ghost", status="dissipated")
    _make_observation(db_session, storm, observation_time=datetime(2017, 6, 1, 12, tzinfo=UTC))
    _make_forecast(db_session, storm, issued_at=datetime(2017, 6, 1, 12, tzinfo=UTC))

    assert active_storm_forecasts(db_session) == []


def test_active_storm_forecasts_skips_storms_without_a_forecast(
    db_session: Session,
) -> None:
    """An active storm whose first forecast hasn't been ingested yet is
    omitted — returning it would force the UI to handle a null-forecast
    branch. As soon as the next forecast ingest tick populates a row,
    the storm appears in the response."""
    storm = _make_storm(db_session, nhc_id="AL992017", name="NewbornInvest")
    _make_observation(db_session, storm, observation_time=datetime(2017, 9, 1, 12, tzinfo=UTC))
    # No Forecast row.

    assert active_storm_forecasts(db_session) == []


def test_active_storm_forecasts_returns_latest_forecast_when_multiple(
    db_session: Session,
) -> None:
    """Two forecasts for the same storm — the endpoint returns the one
    with the most recent ``issued_at`` (NHC advisory boundary, not our
    ingest wall clock)."""
    storm = _make_storm(db_session, nhc_id="AL112017", name="Irma")
    _make_observation(db_session, storm, observation_time=datetime(2017, 9, 9, 15, tzinfo=UTC))

    older_cone = {"type": "Polygon", "coordinates": [[[-70, 20], [-70, 21], [-71, 20], [-70, 20]]]}
    newer_cone = {"type": "Polygon", "coordinates": [[[-80, 22], [-80, 23], [-81, 22], [-80, 22]]]}

    _make_forecast(
        db_session,
        storm,
        issued_at=datetime(2017, 9, 9, 9, tzinfo=UTC),
        cone=older_cone,
    )
    _make_forecast(
        db_session,
        storm,
        issued_at=datetime(2017, 9, 9, 15, tzinfo=UTC),
        cone=newer_cone,
    )

    [payload] = active_storm_forecasts(db_session)
    assert payload["forecast"]["cone_geojson"] == newer_cone


def test_active_storm_forecasts_returns_latest_observation_when_multiple(
    db_session: Session,
) -> None:
    """Three polls of the same advisory: latest (by ``observation_time``)
    wins. Current position on the map is the newest, not the ingest-
    order first."""
    storm = _make_storm(db_session, nhc_id="AL112017", name="Irma")
    _make_observation(
        db_session,
        storm,
        observation_time=datetime(2017, 9, 9, 9, tzinfo=UTC),
        latitude_deg=21.0,
        longitude_deg=-75.0,
    )
    _make_observation(
        db_session,
        storm,
        observation_time=datetime(2017, 9, 9, 15, tzinfo=UTC),
        latitude_deg=22.9,
        longitude_deg=-79.9,
    )
    _make_observation(
        db_session,
        storm,
        observation_time=datetime(2017, 9, 9, 12, tzinfo=UTC),
        latitude_deg=21.8,
        longitude_deg=-77.0,
    )
    _make_forecast(db_session, storm, issued_at=datetime(2017, 9, 9, 15, tzinfo=UTC))

    [payload] = active_storm_forecasts(db_session)
    pos = payload["current_position"]
    assert pos["latitude_deg"] == pytest.approx(22.9)
    assert pos["longitude_deg"] == pytest.approx(-79.9)


def test_active_storm_forecasts_orders_by_nhc_id(db_session: Session) -> None:
    """Two active storms at once — stable ordering by NHC id (AL09 before
    AL11), so the panel UI never swaps them around between polls."""
    storm_a = _make_storm(db_session, nhc_id="AL112017", name="Irma")
    storm_b = _make_storm(db_session, nhc_id="AL092017", name="Harvey")
    for storm in (storm_a, storm_b):
        _make_observation(db_session, storm, observation_time=datetime(2017, 9, 1, 12, tzinfo=UTC))
        _make_forecast(db_session, storm, issued_at=datetime(2017, 9, 1, 12, tzinfo=UTC))

    payloads = active_storm_forecasts(db_session)
    assert [p["storm"]["nhc_id"] for p in payloads] == ["AL092017", "AL112017"]


def test_active_storm_forecasts_omits_wsp_by_default(db_session: Session) -> None:
    """wind_probability_geojson can be multi-MB. Default=exclude, to keep
    the cone-map fetch tight; the key should not even be present in the
    forecast block when ``include_wsp=False``."""
    storm = _make_storm(db_session, nhc_id="AL112017", name="Irma")
    _make_observation(db_session, storm, observation_time=datetime(2017, 9, 9, 15, tzinfo=UTC))
    _make_forecast(
        db_session,
        storm,
        issued_at=datetime(2017, 9, 9, 15, tzinfo=UTC),
        wsp=_DEFAULT_WSP,
    )

    [payload] = active_storm_forecasts(db_session)
    assert "wind_probability_geojson" not in payload["forecast"]


def test_active_storm_forecasts_includes_wsp_when_requested(
    db_session: Session,
) -> None:
    storm = _make_storm(db_session, nhc_id="AL112017", name="Irma")
    _make_observation(db_session, storm, observation_time=datetime(2017, 9, 9, 15, tzinfo=UTC))
    _make_forecast(
        db_session,
        storm,
        issued_at=datetime(2017, 9, 9, 15, tzinfo=UTC),
        wsp=_DEFAULT_WSP,
    )

    [payload] = active_storm_forecasts(db_session, include_wsp=True)
    assert payload["forecast"]["wind_probability_geojson"] == _DEFAULT_WSP


def test_active_storm_forecasts_handles_missing_observation(
    db_session: Session,
) -> None:
    """Corner case: active storm with a forecast but no observation
    (dev DB populated by hand, or an ingest that died between upserting
    Storm+Forecast and flushing Observations). ``current_position`` must
    be null, not missing and not crashing."""
    storm = _make_storm(db_session, nhc_id="AL112017", name="Irma")
    _make_forecast(db_session, storm, issued_at=datetime(2017, 9, 9, 15, tzinfo=UTC))

    [payload] = active_storm_forecasts(db_session)
    assert payload["current_position"] is None
    assert payload["storm"]["nhc_id"] == "AL112017"


def test_payload_shape_contains_expected_top_level_keys(db_session: Session) -> None:
    """Freeze the response contract so the JS side can rely on it."""
    storm = _make_storm(db_session, nhc_id="AL112017", name="Irma")
    _make_observation(db_session, storm, observation_time=datetime(2017, 9, 9, 15, tzinfo=UTC))
    _make_forecast(db_session, storm, issued_at=datetime(2017, 9, 9, 15, tzinfo=UTC))

    [payload] = active_storm_forecasts(db_session)
    assert set(payload.keys()) == {"storm", "current_position", "forecast"}
    assert set(payload["storm"].keys()) == {
        "nhc_id",
        "name",
        "season_year",
        "storm_type",
        "max_wind_kt",
        "min_pressure_mb",
        "status",
    }
    assert set(payload["forecast"].keys()) == {
        "issued_at",
        "cone_geojson",
        "forecast_5day_points",
        "raw_source_url",
    }


# ----- HTTP endpoint -------------------------------------------------------


def test_endpoint_returns_empty_storms_list_off_season(client: TestClient) -> None:
    """Off-season contract: 200 OK + ``{"storms": []}``, not 404. Keeps
    the JS branch-free — the client always sees a list."""
    response = client.get("/api/v1/forecasts/active")
    assert response.status_code == 200
    assert response.json() == {"storms": []}


def test_endpoint_returns_storm_payloads(client: TestClient, db_session: Session) -> None:
    storm = _make_storm(db_session, nhc_id="AL112017", name="Irma")
    _make_observation(db_session, storm, observation_time=datetime(2017, 9, 9, 15, tzinfo=UTC))
    _make_forecast(db_session, storm, issued_at=datetime(2017, 9, 9, 15, tzinfo=UTC))
    db_session.commit()

    response = client.get("/api/v1/forecasts/active")
    assert response.status_code == 200
    body = response.json()
    assert len(body["storms"]) == 1
    payload = body["storms"][0]
    assert payload["storm"]["nhc_id"] == "AL112017"
    assert payload["storm"]["name"] == "Irma"
    assert payload["current_position"]["intensity_kt"] == 125
    assert payload["forecast"]["cone_geojson"]["type"] == "Polygon"


def test_endpoint_honors_include_wsp_query_param(client: TestClient, db_session: Session) -> None:
    storm = _make_storm(db_session, nhc_id="AL112017", name="Irma")
    _make_observation(db_session, storm, observation_time=datetime(2017, 9, 9, 15, tzinfo=UTC))
    _make_forecast(
        db_session,
        storm,
        issued_at=datetime(2017, 9, 9, 15, tzinfo=UTC),
        wsp=_DEFAULT_WSP,
    )
    db_session.commit()

    default = client.get("/api/v1/forecasts/active").json()
    with_wsp = client.get("/api/v1/forecasts/active?include_wsp=true").json()

    assert "wind_probability_geojson" not in default["storms"][0]["forecast"]
    assert with_wsp["storms"][0]["forecast"]["wind_probability_geojson"] == _DEFAULT_WSP
