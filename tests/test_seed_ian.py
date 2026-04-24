"""Tests for the Ian 2022 dev seed CLI.

Parallel to ``test_seed_irma.py`` — same philosophy: tight tests that
lock down the *shape* of what gets written rather than the aesthetics
of the synthesized polygons. The point of the seed is that a developer
can run the dashboard off-season with a populated Panel 4 (landfall
probability), so what matters is:

  * Storm / Observation / Forecast rows land with the right fields.
  * ``forecast.wind_probability_geojson`` is a FeatureCollection whose
    Features carry ``PWIND`` and ``threshold_kt`` properties — the
    shape ``panel_landfall.js`` reads from.
  * The service layer (which the real UI talks to) accepts the seeded
    rows as if they'd come through the production WSP parser.
  * Re-running the seed is idempotent; ``--clear`` does a clean
    delete + re-insert.
"""

from __future__ import annotations

import warnings

from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import Session

from rmn_dashboard.dev.seed_ian import NHC_ID, WSP_BAND_CONFIG, seed
from rmn_dashboard.models import Forecast, Storm, StormObservation
from rmn_dashboard.services.forecasts import active_storm_forecasts


def test_seed_inserts_storm_observation_and_forecast(db_session: Session) -> None:
    summary = seed(db_session)
    db_session.commit()

    storm = db_session.query(Storm).filter_by(nhc_id=NHC_ID).one()
    obs = db_session.query(StormObservation).filter_by(storm_id=storm.id).one()
    forecast = db_session.query(Forecast).filter_by(storm_id=storm.id).one()

    assert storm.name == "Ian"
    assert storm.status == "active"
    assert obs.classification == "HU"
    # Cone shape: same contract Panel 1 consumes — bare Polygon, not a
    # Feature or FeatureCollection.
    assert forecast.cone_geojson["type"] == "Polygon"
    # 5-day points: GeoJSON Features with DBF-style properties.
    points = forecast.forecast_5day_points
    assert isinstance(points, list) and len(points) >= 1
    first = points[0]
    assert first["type"] == "Feature"
    assert first["geometry"]["type"] == "Point"
    assert "MAXWIND" in first["properties"]
    assert "FLDATELBL" in first["properties"]
    # Sanity-check the summary the CLI prints.
    assert summary["storm_id"] == storm.id
    assert summary["forecast_points"] == len(points)


def test_seed_populates_wsp_as_feature_collection(db_session: Session) -> None:
    """The WSP payload — the whole point of the Ian seed — is shaped the
    way Panel 4 expects: a FeatureCollection of Polygon Features, each
    tagged with PWIND and threshold_kt.

    Also verifies we cover all three thresholds (34 / 50 / 64 kt), so
    the threshold dropdown has non-empty layers for each option.
    """
    seed(db_session)
    db_session.commit()

    storm = db_session.query(Storm).filter_by(nhc_id=NHC_ID).one()
    forecast = db_session.query(Forecast).filter_by(storm_id=storm.id).one()
    assert forecast is not None

    wsp = forecast.wind_probability_geojson
    assert wsp is not None, "wind_probability_geojson should be populated by seed_ian"
    assert wsp["type"] == "FeatureCollection"

    features = wsp["features"]
    assert len(features) >= 9  # 3 thresholds × at least 3 bands each in practice

    # Every feature should be a Polygon with the two properties
    # panel_landfall.js filters on.
    thresholds_seen: set[int] = set()
    for feat in features:
        assert feat["type"] == "Feature"
        assert feat["geometry"]["type"] == "Polygon"
        props = feat["properties"]
        assert isinstance(props["PWIND"], int)
        assert props["threshold_kt"] in (34, 50, 64)
        thresholds_seen.add(props["threshold_kt"])

    # All three WSP products must be represented so the UI threshold
    # dropdown has something to show in each position.
    assert thresholds_seen == {34, 50, 64}

    # Feature count should match the config — if someone edits
    # WSP_BAND_CONFIG and forgets a threshold, this catches it.
    expected = sum(len(bands) for _, bands in WSP_BAND_CONFIG)
    assert len(features) == expected


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
    """End-to-end: seed → DB → service returns the WSP in the shape the
    Panel 4 JS consumes.

    Covers the full contract from the CLI through the Forecast row to
    the ``active_storm_forecasts(include_wsp=True)`` payload shape.
    """
    seed(db_session)
    db_session.commit()

    payloads = active_storm_forecasts(db_session, include_wsp=True)
    assert len(payloads) == 1
    entry = payloads[0]

    assert entry["storm"]["nhc_id"] == NHC_ID
    assert entry["current_position"] is not None
    assert entry["forecast"]["cone_geojson"]["type"] == "Polygon"

    wsp = entry["forecast"]["wind_probability_geojson"]
    assert wsp is not None
    assert wsp["type"] == "FeatureCollection"
    assert len(wsp["features"]) >= 9


def test_seed_wsp_is_hidden_when_include_wsp_false(db_session: Session) -> None:
    """Default API path (Panel 1's cone call) must NOT ship the WSP
    payload — it can be multi-MB in production, and the cone map
    doesn't use it. Belt-and-braces check on top of the service-layer
    unit tests.
    """
    seed(db_session)
    db_session.commit()

    payloads = active_storm_forecasts(db_session)  # include_wsp defaults False
    assert "wind_probability_geojson" not in payloads[0]["forecast"]


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

    # Capture SQLAlchemy warnings during the clear+reseed. An "identity
    # map already had an identity for ..." SAWarning here means
    # ``_clear_existing`` forgot to ``expunge_all()`` after deleting,
    # and the next PK-reusing insert tripped the identity-map guard.
    # Silent today — we want it to stay silent.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", SAWarning)
        seed(db_session, clear=True)
        db_session.commit()
    identity_warnings = [w for w in caught if "identity map already had" in str(w.message)]
    assert not identity_warnings, (
        f"_clear_existing should expunge before re-insert; got: "
        f"{[str(w.message) for w in identity_warnings]}"
    )

    storm = db_session.query(Storm).filter_by(nhc_id=NHC_ID).one()
    # ``clear=True`` should have dropped the mutated row and re-inserted
    # the canonical seeded data.
    assert storm.name == "Ian"
    # And only one row — no duplicate from the re-seed.
    assert db_session.query(Storm).filter_by(nhc_id=NHC_ID).count() == 1
