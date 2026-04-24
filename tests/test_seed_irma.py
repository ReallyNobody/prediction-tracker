"""Tests for the Irma 2017 dev seed CLI.

These are tight: the seed exists purely so a developer can run the
dashboard off-season with a populated map. What we actually want to
lock down is the *shape* of what gets written — Storm row present,
Forecast row present with a Polygon cone and GeoJSON-Feature points,
and the service layer (the thing the real UI reads from) accepts the
seeded rows as if they'd come through the production NHC parser.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from rmn_dashboard.dev.seed_irma import NHC_ID, seed
from rmn_dashboard.models import Forecast, Storm, StormObservation
from rmn_dashboard.services.forecasts import active_storm_forecasts


def test_seed_inserts_storm_observation_and_forecast(db_session: Session) -> None:
    summary = seed(db_session)
    db_session.commit()

    storm = db_session.query(Storm).filter_by(nhc_id=NHC_ID).one()
    obs = db_session.query(StormObservation).filter_by(storm_id=storm.id).one()
    forecast = db_session.query(Forecast).filter_by(storm_id=storm.id).one()

    assert storm.name == "Irma"
    assert storm.status == "active"
    assert obs.classification == "HU"
    assert forecast.cone_geojson["type"] == "Polygon"
    # _5day_pts shape: GeoJSON Features with Point geometries and DBF
    # props. forecast_map.js reads FLDATELBL / MAXWIND / TCDVLP.
    points = forecast.forecast_5day_points
    assert isinstance(points, list) and len(points) >= 1
    first = points[0]
    assert first["type"] == "Feature"
    assert first["geometry"]["type"] == "Point"
    assert len(first["geometry"]["coordinates"]) == 2
    assert "MAXWIND" in first["properties"]
    assert "FLDATELBL" in first["properties"]
    # Sanity-check the summary the CLI prints.
    assert summary["storm_id"] == storm.id
    assert summary["forecast_points"] == len(points)


def test_seed_is_idempotent(db_session: Session) -> None:
    """Running the seed twice should not create duplicates."""
    seed(db_session)
    db_session.commit()
    seed(db_session)
    db_session.commit()

    assert db_session.query(Storm).filter_by(nhc_id=NHC_ID).count() == 1
    storm = db_session.query(Storm).filter_by(nhc_id=NHC_ID).one()
    assert db_session.query(StormObservation).filter_by(storm_id=storm.id).count() == 1
    assert db_session.query(Forecast).filter_by(storm_id=storm.id).count() == 1


def test_seeded_forecast_is_visible_to_service(db_session: Session) -> None:
    """The service the API uses returns the seeded storm in its payload.

    Covers the end-to-end contract: seed → DB → active_storm_forecasts →
    the dict shape Panel 1's JS will actually consume.
    """
    seed(db_session)
    db_session.commit()

    payloads = active_storm_forecasts(db_session)
    assert len(payloads) == 1
    entry = payloads[0]
    assert entry["storm"]["nhc_id"] == NHC_ID
    assert entry["current_position"] is not None
    assert entry["forecast"]["cone_geojson"]["type"] == "Polygon"
    assert len(entry["forecast"]["forecast_5day_points"]) >= 1


def test_seed_clear_flag_replaces_previous_row(db_session: Session) -> None:
    """``--clear`` drops the old Storm row (and cascades) before re-seeding.

    We prove the replacement by mutating the seeded row, running the
    seed with ``clear=True``, and asserting the mutation is gone. A PK
    check would be wrong here — SQLite without AUTOINCREMENT reuses
    ROWIDs after a delete, so the surrogate ID can legitimately stay
    at 1 even when the row was actually replaced.
    """
    seed(db_session)
    db_session.commit()
    storm = db_session.query(Storm).filter_by(nhc_id=NHC_ID).one()
    storm.name = "MUTATED"
    db_session.commit()

    seed(db_session, clear=True)
    db_session.commit()

    storm = db_session.query(Storm).filter_by(nhc_id=NHC_ID).one()
    # ``clear=True`` should have dropped the mutated row and re-inserted
    # the canonical seeded data.
    assert storm.name == "Irma"
    # And only one row — no duplicate from the re-seed.
    assert db_session.query(Storm).filter_by(nhc_id=NHC_ID).count() == 1
