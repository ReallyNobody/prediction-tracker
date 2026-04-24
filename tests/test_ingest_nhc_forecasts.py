"""Unit tests for the NHC forecast-product ingestion task.

Network-free: httpx requests route through ``MockTransport`` with a
URL→bytes lookup table so one storm's fetch lands a forecastTrack ZIP
and another's lands a wsp ZIP from the same ``MockTransport``.

DB is the in-memory SQLite ``db_session`` fixture from conftest.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from rmn_dashboard.models import Forecast, Storm, StormObservation
from rmn_dashboard.tasks.ingest_nhc_forecasts import run_nhc_forecast_ingest

# Reuse the synthetic-fixture builders from test_nhc_shapefiles so the
# two test modules agree on schema expectations.
from tests.test_nhc_shapefiles import (  # noqa: E402 — test helper import
    _build_forecast_track_zip,
    _build_wind_probability_zip,
)


# ----- Mock HTTP transport keyed by URL -----------------------------------


def _client_for_url_map(
    url_map: dict[str, bytes],
    *,
    on_unknown: str = "raise",
) -> httpx.Client:
    """Build an httpx.Client whose MockTransport looks up the request URL
    in ``url_map`` and returns the mapped bytes with a 200.

    ``on_unknown``:
      * ``"raise"`` — unknown URL is a test-author bug; fail loudly.
      * ``"404"`` — return a 404 (for testing graceful fetch errors).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in url_map:
            return httpx.Response(200, content=url_map[url])
        if on_unknown == "404":
            return httpx.Response(404, content=b"not found")
        raise AssertionError(f"Unexpected URL in test: {url}")

    return httpx.Client(transport=httpx.MockTransport(handler))


# ----- DB seeding helpers -------------------------------------------------


def _make_storm(
    db: Session,
    *,
    nhc_id: str = "al112017",
    name: str = "Irma",
    status: str = "active",
) -> Storm:
    storm = Storm(
        nhc_id=nhc_id,
        name=name,
        season_year=int(nhc_id[-4:]),
        storm_type="Hurricane",
        max_wind_kt=160,
        min_pressure_mb=927,
        status=status,
    )
    db.add(storm)
    db.flush()
    return storm


def _make_observation(
    db: Session,
    storm: Storm,
    *,
    observation_time: datetime | None = None,
    advisory_urls: dict[str, Any] | None = None,
) -> StormObservation:
    if observation_time is None:
        observation_time = datetime(2017, 9, 9, 15, 0, tzinfo=UTC)
    obs = StormObservation(
        storm_id=storm.id,
        classification="HU",
        intensity_kt=160,
        pressure_mb=927,
        latitude_deg=22.9,
        longitude_deg=-79.9,
        observation_time=observation_time,
        advisory_urls=advisory_urls,
    )
    db.add(obs)
    db.flush()
    return obs


_IRMA_TRACK_URL = (
    "https://www.nhc.noaa.gov/gis/forecast/archive/al112017_5day_038.zip"
)
_ATL_WSP_URL = (
    "https://www.nhc.noaa.gov/gis/forecast/archive/2017_wsp_120hrhalfDeg_118.zip"
)


def _default_advisory_urls(
    track_url: str = _IRMA_TRACK_URL,
    wsp_url: str | None = _ATL_WSP_URL,
) -> dict[str, Any]:
    urls: dict[str, Any] = {"forecastTrack": {"zipFile": track_url, "advNum": "038"}}
    if wsp_url is not None:
        urls["windSpeedProbabilitiesGIS"] = {"zipFile": wsp_url, "advNum": "038"}
    return urls


# ----- Off-season / empty ---------------------------------------------------


def test_run_nhc_forecast_ingest_off_season_returns_zero(db_session: Session) -> None:
    """No active storms → zero forecasts, no warnings, no HTTP calls."""
    count = run_nhc_forecast_ingest(
        db_session,
        # Client still required in signature; unknown URLs would raise.
        http_client=_client_for_url_map({}),
    )
    assert count == 0
    assert db_session.scalar(select(Forecast)) is None


# ----- Happy paths ---------------------------------------------------------


def test_run_nhc_forecast_ingest_inserts_new_forecast(db_session: Session) -> None:
    storm = _make_storm(db_session)
    _make_observation(db_session, storm, advisory_urls=_default_advisory_urls())

    track_zip = _build_forecast_track_zip()
    wsp_zip = _build_wind_probability_zip()

    count = run_nhc_forecast_ingest(
        db_session,
        http_client=_client_for_url_map(
            {_IRMA_TRACK_URL: track_zip, _ATL_WSP_URL: wsp_zip}
        ),
    )
    assert count == 1

    forecast = db_session.scalars(select(Forecast)).one()
    assert forecast.storm_id == storm.id
    assert forecast.issued_at.replace(tzinfo=None) == datetime(2017, 9, 9, 15, 0)
    assert forecast.cone_geojson["type"] == "Polygon"
    assert len(forecast.forecast_5day_points) == 3
    assert forecast.wind_probability_geojson["type"] == "FeatureCollection"
    assert len(forecast.wind_probability_geojson["features"]) == 3
    assert forecast.raw_source_url == _IRMA_TRACK_URL


def test_run_nhc_forecast_ingest_idempotent_on_same_advisory(db_session: Session) -> None:
    """Running twice on the same advisory must not create two rows.

    NHC updates `CurrentStorms.json` (and therefore the URL we poll off)
    minutes before a new advisory is actually issued — the 15-min
    cadence will routinely re-fetch the same ZIP. The (storm_id,
    issued_at) UPSERT handles that.
    """
    storm = _make_storm(db_session)
    _make_observation(db_session, storm, advisory_urls=_default_advisory_urls())

    track_zip = _build_forecast_track_zip()
    wsp_zip = _build_wind_probability_zip()

    first = run_nhc_forecast_ingest(
        db_session,
        http_client=_client_for_url_map(
            {_IRMA_TRACK_URL: track_zip, _ATL_WSP_URL: wsp_zip}
        ),
    )
    second = run_nhc_forecast_ingest(
        db_session,
        http_client=_client_for_url_map(
            {_IRMA_TRACK_URL: track_zip, _ATL_WSP_URL: wsp_zip}
        ),
    )

    assert first == 1
    assert second == 0  # refreshed, not inserted
    forecasts = db_session.scalars(select(Forecast)).all()
    assert len(forecasts) == 1


def test_run_nhc_forecast_ingest_dedupes_wsp_fetches_across_storms(
    db_session: Session,
) -> None:
    """Two Atlantic storms sharing a wsp URL must fetch it exactly once.

    Deduping matters when NHC has 4+ storms simultaneously — without the
    per-URL cache we'd re-fetch the same basin-level wsp ZIP for every
    storm, quadrupling egress and upstream load.
    """
    irma = _make_storm(db_session, nhc_id="al112017", name="Irma")
    _make_observation(
        db_session, irma, advisory_urls=_default_advisory_urls()
    )
    jose = _make_storm(db_session, nhc_id="al122017", name="Jose")
    jose_track_url = (
        "https://www.nhc.noaa.gov/gis/forecast/archive/al122017_5day_012.zip"
    )
    _make_observation(
        db_session,
        jose,
        advisory_urls=_default_advisory_urls(
            track_url=jose_track_url, wsp_url=_ATL_WSP_URL
        ),
        observation_time=datetime(2017, 9, 9, 15, 0, tzinfo=UTC) + timedelta(seconds=1),
    )

    # Give Jose a distinct advisory so both storms generate distinct rows.
    jose_track_zip = _build_forecast_track_zip(
        storm_id="al122017", advisory_number="012", advdate="170909 1500"
    )

    fetch_counts: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        fetch_counts[url] = fetch_counts.get(url, 0) + 1
        if url == _IRMA_TRACK_URL:
            return httpx.Response(200, content=_build_forecast_track_zip())
        if url == jose_track_url:
            return httpx.Response(200, content=jose_track_zip)
        if url == _ATL_WSP_URL:
            return httpx.Response(200, content=_build_wind_probability_zip())
        raise AssertionError(f"Unexpected URL: {url}")

    count = run_nhc_forecast_ingest(
        db_session,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert count == 2
    # Each storm's forecastTrack fetched once; shared wsp fetched once.
    assert fetch_counts[_IRMA_TRACK_URL] == 1
    assert fetch_counts[jose_track_url] == 1
    assert fetch_counts[_ATL_WSP_URL] == 1


# ----- Partial-failure paths ----------------------------------------------


def test_run_nhc_forecast_ingest_skips_storm_without_observations(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A Storm row flagged active with no observations yet must not
    crash the batch. This is a dev-seed corner case; we log and skip."""
    _make_storm(db_session, nhc_id="al112017", name="Irma")
    # No observation rows inserted.

    import logging as _logging

    with caplog.at_level(_logging.WARNING, logger="rmn_dashboard.tasks.ingest_nhc_forecasts"):
        count = run_nhc_forecast_ingest(
            db_session, http_client=_client_for_url_map({})
        )

    assert count == 0
    assert db_session.scalar(select(Forecast)) is None
    assert any("no observations" in r.message for r in caplog.records)


def test_run_nhc_forecast_ingest_skips_storm_without_forecast_track_url(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A storm whose latest advisory_urls doesn't include forecastTrack
    gets skipped with a warning — not every advisory type ships spatial
    products (e.g. Special Advisories early in lifecycle)."""
    storm = _make_storm(db_session)
    _make_observation(
        db_session,
        storm,
        advisory_urls={"publicAdvisory": {"advNum": "38"}},  # no forecastTrack
    )

    import logging as _logging

    with caplog.at_level(_logging.WARNING, logger="rmn_dashboard.tasks.ingest_nhc_forecasts"):
        count = run_nhc_forecast_ingest(
            db_session, http_client=_client_for_url_map({})
        )

    assert count == 0
    assert any("no forecastTrack.zipFile" in r.message for r in caplog.records)


def test_run_nhc_forecast_ingest_skips_storm_on_http_error(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Upstream 404 on the track ZIP must not poison the batch — the
    storm is skipped, the ingest commits whatever else succeeded."""
    irma = _make_storm(db_session, nhc_id="al112017", name="Irma")
    _make_observation(db_session, irma, advisory_urls=_default_advisory_urls())
    jose = _make_storm(db_session, nhc_id="al122017", name="Jose")
    jose_track_url = (
        "https://www.nhc.noaa.gov/gis/forecast/archive/al122017_5day_012.zip"
    )
    _make_observation(
        db_session,
        jose,
        advisory_urls=_default_advisory_urls(
            track_url=jose_track_url, wsp_url=_ATL_WSP_URL
        ),
        observation_time=datetime(2017, 9, 9, 15, 0, tzinfo=UTC) + timedelta(seconds=1),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == _IRMA_TRACK_URL:
            return httpx.Response(503, content=b"maintenance")
        if url == jose_track_url:
            return httpx.Response(
                200,
                content=_build_forecast_track_zip(
                    storm_id="al122017", advisory_number="012"
                ),
            )
        if url == _ATL_WSP_URL:
            return httpx.Response(200, content=_build_wind_probability_zip())
        raise AssertionError(f"Unexpected URL: {url}")

    import logging as _logging

    with caplog.at_level(_logging.ERROR, logger="rmn_dashboard.tasks.ingest_nhc_forecasts"):
        count = run_nhc_forecast_ingest(
            db_session,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

    # Only Jose succeeded.
    assert count == 1
    forecast = db_session.scalars(select(Forecast)).one()
    assert forecast.storm_id == jose.id
    assert any("failed to fetch/parse forecastTrack" in r.message for r in caplog.records)


def test_run_nhc_forecast_ingest_keeps_forecast_when_wsp_fails(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A broken wsp ZIP must not cancel the forecastTrack insert — the
    forecast lands with wind_probability_geojson=None."""
    storm = _make_storm(db_session)
    _make_observation(db_session, storm, advisory_urls=_default_advisory_urls())

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == _IRMA_TRACK_URL:
            return httpx.Response(200, content=_build_forecast_track_zip())
        if url == _ATL_WSP_URL:
            return httpx.Response(200, content=b"corrupt not-a-zip")
        raise AssertionError(f"Unexpected URL: {url}")

    import logging as _logging

    with caplog.at_level(_logging.ERROR, logger="rmn_dashboard.tasks.ingest_nhc_forecasts"):
        count = run_nhc_forecast_ingest(
            db_session,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

    assert count == 1
    forecast = db_session.scalars(select(Forecast)).one()
    assert forecast.cone_geojson is not None
    assert forecast.wind_probability_geojson is None
    assert any("failed to fetch/parse wsp" in r.message for r in caplog.records)


def test_run_nhc_forecast_ingest_handles_missing_wsp_url(db_session: Session) -> None:
    """When a storm's advisory_urls simply omits windSpeedProbabilitiesGIS,
    the forecast still lands — wsp is optional."""
    storm = _make_storm(db_session)
    _make_observation(
        db_session,
        storm,
        advisory_urls=_default_advisory_urls(wsp_url=None),
    )

    count = run_nhc_forecast_ingest(
        db_session,
        http_client=_client_for_url_map(
            {_IRMA_TRACK_URL: _build_forecast_track_zip()}
        ),
    )
    assert count == 1
    forecast = db_session.scalars(select(Forecast)).one()
    assert forecast.wind_probability_geojson is None


def test_run_nhc_forecast_ingest_only_processes_active_storms(
    db_session: Session,
) -> None:
    """A Storm row with status='dissipated' is skipped — we don't
    re-fetch forecasts for storms NHC has stopped publishing on."""
    _make_storm(db_session, nhc_id="al112017", name="Irma", status="dissipated")

    count = run_nhc_forecast_ingest(
        db_session, http_client=_client_for_url_map({})
    )
    assert count == 0
    assert db_session.scalar(select(Forecast)) is None


def test_run_nhc_forecast_ingest_logs_counts_at_info(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    storm = _make_storm(db_session)
    _make_observation(db_session, storm, advisory_urls=_default_advisory_urls())

    import logging as _logging

    with caplog.at_level(_logging.INFO, logger="rmn_dashboard.tasks.ingest_nhc_forecasts"):
        run_nhc_forecast_ingest(
            db_session,
            http_client=_client_for_url_map(
                {
                    _IRMA_TRACK_URL: _build_forecast_track_zip(),
                    _ATL_WSP_URL: _build_wind_probability_zip(),
                }
            ),
        )

    assert any(
        "storms processed" in r.message and "inserted" in r.message
        for r in caplog.records
    )
