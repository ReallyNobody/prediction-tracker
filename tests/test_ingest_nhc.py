"""Unit tests for the NHC ingestion task.

Network-free: the scraper uses httpx ``MockTransport``, and the DB is the
in-memory SQLite ``db_session`` fixture from ``conftest.py``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from rmn_dashboard.models import Storm, StormObservation
from rmn_dashboard.tasks.ingest_nhc import (
    _expand_classification,
    _season_year_from_nhc_id,
    run_nhc_ingest,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "nhc_current_storms.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def _client_for(payload: dict) -> httpx.Client:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _client_for_fn(fn: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(fn))


# ----- Pure helpers --------------------------------------------------------


def test_expand_classification_known_codes() -> None:
    assert _expand_classification("HU") == "Hurricane"
    assert _expand_classification("TS") == "Tropical Storm"
    assert _expand_classification("PTC") == "Post-Tropical Cyclone"


def test_expand_classification_unknown_passthrough() -> None:
    # NHC introduces new codes very occasionally; passing through is
    # better than silently dropping information.
    assert _expand_classification("NEW_CODE") == "NEW_CODE"


def test_season_year_from_nhc_id_extracts_year() -> None:
    assert _season_year_from_nhc_id("al112017") == 2017
    assert _season_year_from_nhc_id("ep052023") == 2023


def test_season_year_from_nhc_id_rejects_wrong_shape() -> None:
    with pytest.raises(ValueError):
        _season_year_from_nhc_id("irma")
    with pytest.raises(ValueError):
        _season_year_from_nhc_id("al11abcd")


# ----- run_nhc_ingest ------------------------------------------------------


def test_run_nhc_ingest_creates_storms_and_observations(db_session: Session) -> None:
    fixture = _load_fixture()

    count = run_nhc_ingest(db_session, http_client=_client_for(fixture))

    # Two storms in the fixture → two observations + two Storm identity rows.
    assert count == 2

    storms = db_session.scalars(select(Storm).order_by(Storm.nhc_id)).all()
    assert [s.nhc_id for s in storms] == ["al112017", "al122017"]
    assert [s.name for s in storms] == ["Irma", "Jose"]
    assert [s.storm_type for s in storms] == ["Hurricane", "Hurricane"]
    assert [s.season_year for s in storms] == [2017, 2017]
    assert [s.status for s in storms] == ["active", "active"]
    assert [s.max_wind_kt for s in storms] == [160, 125]
    assert [s.min_pressure_mb for s in storms] == [927, 948]

    obs = db_session.scalars(select(StormObservation)).all()
    assert len(obs) == 2
    irma = next(o for o in obs if o.storm.nhc_id == "al112017")
    assert irma.classification == "HU"
    assert irma.intensity_kt == 160
    assert irma.pressure_mb == 927
    assert irma.latitude_deg == 22.9
    assert irma.longitude_deg == -79.9
    assert irma.movement_dir_deg == 315
    assert irma.movement_speed_mph == 15
    # Advisory URLs captured verbatim for Day 10 consumption.
    assert irma.advisory_urls is not None
    assert "forecastTrack" in irma.advisory_urls
    assert (
        irma.advisory_urls["forecastTrack"]["zipFile"]
        == "https://www.nhc.noaa.gov/gis/forecast/archive/al112017_5day_038.zip"
    )


def test_run_nhc_ingest_off_season_returns_zero(db_session: Session) -> None:
    """Empty activeStorms is a normal state, not an error — no rows, no warning."""
    count = run_nhc_ingest(db_session, http_client=_client_for({"activeStorms": []}))
    assert count == 0
    assert db_session.scalar(select(Storm)) is None
    assert db_session.scalar(select(StormObservation)) is None


def test_run_nhc_ingest_idempotent_on_same_advisory(db_session: Session) -> None:
    """Polling twice within the same NHC advisory must not double-insert.

    CurrentStorms.json updates several minutes before a new advisory is
    actually issued — running the 15-min cadence will routinely hit the
    same ``lastUpdate`` twice. The (storm_id, observation_time) dedupe
    check is what keeps that from polluting the snapshot timeline.
    """
    fixture = _load_fixture()
    first = run_nhc_ingest(db_session, http_client=_client_for(fixture))
    second = run_nhc_ingest(db_session, http_client=_client_for(fixture))

    assert first == 2
    assert second == 0  # everything was a dupe
    assert db_session.scalar(select(StormObservation).where(StormObservation.storm_id.is_not(None))) is not None
    assert len(db_session.scalars(select(StormObservation)).all()) == 2


def test_run_nhc_ingest_new_advisory_adds_observation_updates_storm(
    db_session: Session,
) -> None:
    """A later poll with a new lastUpdate and a higher intensity should:
    (a) add a new StormObservation row, (b) raise Storm.max_wind_kt.
    """
    fixture = _load_fixture()
    run_nhc_ingest(db_session, http_client=_client_for(fixture))

    # Second tick: Irma strengthens and NHC publishes a fresh advisory.
    # (Using a minimal mutation to keep the test focused.)
    newer = json.loads(FIXTURE_PATH.read_text())
    irma = newer["activeStorms"][0]
    irma["intensity"] = 180
    irma["pressure"] = 914
    irma["lastUpdate"] = (
        datetime.fromisoformat(irma["lastUpdate"].replace("Z", "+00:00"))
        + timedelta(hours=3)
    ).isoformat()

    run_nhc_ingest(db_session, http_client=_client_for(newer))

    irma_storm = db_session.scalars(
        select(Storm).where(Storm.nhc_id == "al112017")
    ).one()
    assert irma_storm.max_wind_kt == 180  # lifetime peak raised
    assert irma_storm.min_pressure_mb == 914  # lifetime deepest

    obs_count = len(
        db_session.scalars(
            select(StormObservation).where(StormObservation.storm_id == irma_storm.id)
        ).all()
    )
    assert obs_count == 2  # first tick + strengthened tick


def test_run_nhc_ingest_tracks_invest_to_named_rename(db_session: Session) -> None:
    """Storm names change when NHC upgrades an Invest to a named storm; the
    ``Storm`` row should update in place rather than create a second identity.
    """
    first_payload = {
        "activeStorms": [
            {
                "id": "al992026",
                "binNumber": "AT9",
                "name": "Invest 99L",
                "classification": "TD",
                "intensity": 30,
                "latitude": "15.0N",
                "latitude_numeric": 15.0,
                "longitude": "45.0W",
                "longitude_numeric": -45.0,
                "movementDir": 280,
                "movementSpeed": 10,
                "lastUpdate": "2026-08-20T15:00:00Z",
            }
        ]
    }
    second_payload = {
        "activeStorms": [
            {
                **first_payload["activeStorms"][0],
                "name": "Ada",
                "classification": "TS",
                "intensity": 50,
                "lastUpdate": "2026-08-20T21:00:00Z",
            }
        ]
    }

    run_nhc_ingest(db_session, http_client=_client_for(first_payload))
    run_nhc_ingest(db_session, http_client=_client_for(second_payload))

    storms = db_session.scalars(select(Storm).where(Storm.nhc_id == "al992026")).all()
    assert len(storms) == 1  # identity preserved; no duplicate Storm row
    storm = storms[0]
    assert storm.name == "Ada"  # name refreshed
    assert storm.storm_type == "Tropical Storm"  # classification refreshed
    assert storm.max_wind_kt == 50  # lifetime peak raised

    obs_count = len(
        db_session.scalars(
            select(StormObservation).where(StormObservation.storm_id == storm.id)
        ).all()
    )
    assert obs_count == 2  # one per advisory


def test_run_nhc_ingest_skips_storm_with_malformed_nhc_id(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A storm whose id doesn't parse as season-year must be skipped, not
    crash the whole ingest batch."""
    payload = {
        "activeStorms": [
            {
                "id": "BADFORMAT",  # not 8 chars + 4-digit year
                "name": "Broken",
                "classification": "TS",
                "intensity": 50,
                "latitude_numeric": 15.0,
                "longitude_numeric": -45.0,
                "lastUpdate": "2026-08-20T15:00:00Z",
            },
            {
                "id": "al012026",
                "name": "Valid",
                "classification": "TS",
                "intensity": 45,
                "latitude_numeric": 14.0,
                "longitude_numeric": -44.0,
                "lastUpdate": "2026-08-20T15:00:00Z",
            },
        ]
    }

    import logging as _logging

    with caplog.at_level(_logging.ERROR, logger="rmn_dashboard.tasks.ingest_nhc"):
        count = run_nhc_ingest(db_session, http_client=_client_for(payload))

    # Only the valid storm was persisted.
    assert count == 1
    names = [s.name for s in db_session.scalars(select(Storm)).all()]
    assert names == ["Valid"]
    assert any("malformed id" in r.message for r in caplog.records)


def test_run_nhc_ingest_logs_counts_at_info(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixture = _load_fixture()
    import logging as _logging

    with caplog.at_level(_logging.INFO, logger="rmn_dashboard.tasks.ingest_nhc"):
        run_nhc_ingest(db_session, http_client=_client_for(fixture))

    # One INFO line summarizing observed / persisted / skipped.
    assert any(
        "storms observed" in r.message and "persisted" in r.message
        for r in caplog.records
    )


def test_run_nhc_ingest_uses_provided_observation_time(db_session: Session) -> None:
    """observation_time must be NHC's lastUpdate, not our wall clock — so
    duplicate polls of the same advisory dedupe correctly.

    SQLite strips tzinfo on round-trip (Postgres preserves it); coerce
    naive→UTC-aware so the assertion works against both backends.
    """
    fixture = _load_fixture()
    run_nhc_ingest(db_session, http_client=_client_for(fixture))

    obs = db_session.scalars(select(StormObservation).limit(1)).one()
    stored = obs.observation_time
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=UTC)
    assert stored == datetime(2017, 9, 9, 15, 0, tzinfo=UTC)
