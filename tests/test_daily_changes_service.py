"""Tests for ``services/daily_changes.todays_changes``.

Lock down what Panel 6 will rely on:

  * The payload shape (top-level keys + per-line keys) the JS targets.
  * The storm-delta logic — intensified / weakened / reclassified /
    newly tracked / no-change-skipped.
  * The equity-mover sort order (top |change_percent|) and limit.
  * The cat bond proxy is reported separately from equities, even
    though it lives in the same TickerQuote table.
  * Quiet days return empty lists / null cat_bond — no fake activity.

The service uses the bundled hurricane_universe.yaml directly (loads
via ``load_universe()`` with no arg). Tests work against that — every
ticker referenced below is a real entry in the bundled file.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from rmn_dashboard.data.universe import load_universe
from rmn_dashboard.models import Storm, StormObservation, TickerQuote
from rmn_dashboard.services.daily_changes import todays_changes


# Reset the loader cache before each test so a different test file's
# fixture-loaded universe doesn't bleed in.
@pytest.fixture(autouse=True)
def _clear_universe_cache() -> None:
    load_universe.cache_clear()


def _add_quote(
    db: Session,
    ticker: str,
    *,
    last_price: float,
    change_percent: float | None,
    minutes_ago: int = 0,
) -> None:
    base = datetime(2026, 4, 25, 17, 0, tzinfo=UTC)
    db.add(
        TickerQuote(
            ticker=ticker,
            last_price=last_price,
            prior_close=last_price - 1 if change_percent else None,
            change_amount=1.0 if change_percent else None,
            change_percent=change_percent,
            currency="USD",
            source="test",
            as_of=base - timedelta(minutes=minutes_ago),
        )
    )


def _add_storm(
    db: Session,
    *,
    nhc_id: str,
    name: str,
    status: str = "active",
    season_year: int = 2026,
) -> Storm:
    # season_year is NOT NULL on the Storm model — every test below uses
    # an NHC id ending in 2026 (AL012026, AL022026, ...), so the default
    # matches the rest of the fixture data. Override only if a future
    # test deliberately seeds a multi-season scenario.
    storm = Storm(
        nhc_id=nhc_id,
        name=name,
        season_year=season_year,
        storm_type="Hurricane",
        max_wind_kt=80,
        min_pressure_mb=970,
        status=status,
        genesis_date=date(2026, 9, 20),
    )
    db.add(storm)
    db.flush()
    return storm


def _add_obs(
    db: Session,
    storm: Storm,
    *,
    intensity_kt: int,
    classification: str = "HU",
    hours_ago: float,
) -> None:
    base = datetime(2026, 4, 25, 17, 0, tzinfo=UTC)
    db.add(
        StormObservation(
            storm_id=storm.id,
            classification=classification,
            intensity_kt=intensity_kt,
            pressure_mb=970,
            latitude_deg=22.0,
            longitude_deg=-79.0,
            observation_time=base - timedelta(hours=hours_ago),
        )
    )


_FIXED_NOW = datetime(2026, 4, 25, 17, 0, tzinfo=UTC)


# --- Top-level shape ------------------------------------------------------


def test_payload_shape_has_expected_keys(db_session: Session) -> None:
    payload = todays_changes(db_session, now=_FIXED_NOW)
    assert set(payload.keys()) == {"as_of", "storms", "equities", "cat_bond"}
    assert isinstance(payload["as_of"], str)
    assert isinstance(payload["storms"], list)
    assert isinstance(payload["equities"], list)
    # cat_bond is dict-or-None, never a list.
    assert payload["cat_bond"] is None or isinstance(payload["cat_bond"], dict)


def test_quiet_day_returns_empty_lists_and_null_cat_bond(
    db_session: Session,
) -> None:
    """No active storms, no quotes seeded → all empty / null."""
    payload = todays_changes(db_session, now=_FIXED_NOW)
    assert payload["storms"] == []
    assert payload["equities"] == []
    assert payload["cat_bond"] is None


# --- Storm-delta logic ---------------------------------------------------


def test_storm_intensified_produces_intensified_kind(db_session: Session) -> None:
    storm = _add_storm(db_session, nhc_id="AL012026", name="Alpha")
    _add_obs(db_session, storm, intensity_kt=80, hours_ago=24)
    _add_obs(db_session, storm, intensity_kt=95, hours_ago=0)
    db_session.commit()

    payload = todays_changes(db_session, now=_FIXED_NOW)
    storms = payload["storms"]
    assert len(storms) == 1
    line = storms[0]
    assert line["kind"] == "intensified"
    assert line["name"] == "Alpha"
    assert "+15 kt" in line["headline"]
    assert "95 kt" in line["headline"]


def test_storm_weakened_produces_weakened_kind(db_session: Session) -> None:
    storm = _add_storm(db_session, nhc_id="AL022026", name="Bravo")
    _add_obs(db_session, storm, intensity_kt=110, hours_ago=24)
    _add_obs(db_session, storm, intensity_kt=85, hours_ago=0)
    db_session.commit()

    payload = todays_changes(db_session, now=_FIXED_NOW)
    line = payload["storms"][0]
    assert line["kind"] == "weakened"
    assert "-25 kt" in line["headline"]


def test_storm_reclassification_uses_reclassified_kind(
    db_session: Session,
) -> None:
    storm = _add_storm(db_session, nhc_id="AL032026", name="Charlie")
    _add_obs(db_session, storm, intensity_kt=55, classification="TS", hours_ago=24)
    _add_obs(db_session, storm, intensity_kt=70, classification="HU", hours_ago=0)
    db_session.commit()

    payload = todays_changes(db_session, now=_FIXED_NOW)
    line = payload["storms"][0]
    assert line["kind"] == "reclassified"
    assert "TS" in line["headline"]
    assert "HU" in line["headline"]


def test_storm_with_only_one_observation_reports_newly_tracked(
    db_session: Session,
) -> None:
    """A storm that just appeared in NHC's feed has no prior obs in
    the lookback window — render as 'newly tracked' rather than
    silently dropping it.
    """
    storm = _add_storm(db_session, nhc_id="AL042026", name="Delta")
    _add_obs(db_session, storm, intensity_kt=45, classification="TS", hours_ago=0)
    db_session.commit()

    payload = todays_changes(db_session, now=_FIXED_NOW)
    line = payload["storms"][0]
    assert line["kind"] == "new"
    assert "Delta" in line["headline"]
    assert "newly tracked" in line["headline"]


def test_storm_with_no_meaningful_change_is_omitted(db_session: Session) -> None:
    """+/- 4 kt + same classification = noise; the line should not
    show. Otherwise a 5-storm basin would always print 5 'unchanged'
    lines and bury real moves.
    """
    storm = _add_storm(db_session, nhc_id="AL052026", name="Echo")
    _add_obs(db_session, storm, intensity_kt=80, hours_ago=24)
    _add_obs(db_session, storm, intensity_kt=82, hours_ago=0)
    db_session.commit()

    payload = todays_changes(db_session, now=_FIXED_NOW)
    assert payload["storms"] == []


def test_dissipated_storms_are_excluded(db_session: Session) -> None:
    """The query filters to Storm.status == 'active'."""
    storm = _add_storm(db_session, nhc_id="AL062026", name="Foxtrot", status="dissipated")
    _add_obs(db_session, storm, intensity_kt=50, hours_ago=24)
    _add_obs(db_session, storm, intensity_kt=80, hours_ago=0)
    db_session.commit()

    payload = todays_changes(db_session, now=_FIXED_NOW)
    assert payload["storms"] == []


# --- Equity movers --------------------------------------------------------


def test_equities_sorted_by_abs_change_percent(db_session: Session) -> None:
    _add_quote(db_session, "UVE", last_price=20.0, change_percent=4.2)
    _add_quote(db_session, "HCI", last_price=40.0, change_percent=-1.0)
    _add_quote(db_session, "NEE", last_price=80.0, change_percent=-5.5)
    _add_quote(db_session, "LEN", last_price=100.0, change_percent=0.5)
    db_session.commit()

    payload = todays_changes(db_session, now=_FIXED_NOW)
    movers = payload["equities"]
    # Sorted by absolute change_percent: NEE (-5.5), UVE (+4.2), HCI (-1.0).
    # LEN (+0.5) drops off the top-3.
    assert [m["ticker"] for m in movers] == ["NEE", "UVE", "HCI"]
    assert movers[0]["change_percent"] == -5.5


def test_equities_excludes_cat_bond_etf(db_session: Session) -> None:
    """ILS lives in TickerQuote alongside the equities, but it should
    NEVER appear in the equity-mover list — it has its own slot.
    """
    _add_quote(db_session, "UVE", last_price=20.0, change_percent=2.0)
    _add_quote(db_session, "ILS", last_price=22.0, change_percent=10.0)
    db_session.commit()

    payload = todays_changes(db_session, now=_FIXED_NOW)
    tickers = {m["ticker"] for m in payload["equities"]}
    assert "ILS" not in tickers
    assert "UVE" in tickers


def test_equities_skips_rows_with_no_change_percent(db_session: Session) -> None:
    """A new IPO with no prior_close yields change_percent=None — the
    row is in the universe but shouldn't appear in 'top movers' since
    there's nothing to rank.
    """
    _add_quote(db_session, "UVE", last_price=20.0, change_percent=None)
    _add_quote(db_session, "HCI", last_price=40.0, change_percent=2.0)
    db_session.commit()

    payload = todays_changes(db_session, now=_FIXED_NOW)
    tickers = {m["ticker"] for m in payload["equities"]}
    assert tickers == {"HCI"}


def test_equity_headline_format(db_session: Session) -> None:
    _add_quote(db_session, "UVE", last_price=20.0, change_percent=4.2)
    db_session.commit()

    payload = todays_changes(db_session, now=_FIXED_NOW)
    line = payload["equities"][0]
    # The JS splits on the em-dash; the server must produce one.
    assert "—" in line["headline"]
    assert line["headline"].startswith("UVE +4.20%")


# --- Cat bond proxy ------------------------------------------------------


def test_cat_bond_reported_separately_when_quote_exists(
    db_session: Session,
) -> None:
    _add_quote(db_session, "ILS", last_price=22.0, change_percent=-0.8)
    db_session.commit()

    payload = todays_changes(db_session, now=_FIXED_NOW)
    cb = payload["cat_bond"]
    assert cb is not None
    assert cb["ticker"] == "ILS"
    assert cb["change_percent"] == -0.8
    assert "—" in cb["headline"]
    assert "cat bond proxy" in cb["headline"]


def test_cat_bond_is_none_when_no_quote_yet(db_session: Session) -> None:
    """Universe has ILS but no scrape has produced a row → null."""
    payload = todays_changes(db_session, now=_FIXED_NOW)
    assert payload["cat_bond"] is None


def test_cat_bond_is_none_when_change_percent_missing(
    db_session: Session,
) -> None:
    _add_quote(db_session, "ILS", last_price=22.0, change_percent=None)
    db_session.commit()

    payload = todays_changes(db_session, now=_FIXED_NOW)
    assert payload["cat_bond"] is None
