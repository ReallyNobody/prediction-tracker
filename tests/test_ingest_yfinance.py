"""Tests for the yfinance ingest task.

Mirrors ``test_ingest_kalshi.py``: we feed the task an injected
``fetch_one`` so it never touches Yahoo, then assert the rows that
land in the DB match the snapshots we expect.

Lock-down points:

  * Each successful snapshot writes one ``TickerQuote`` row.
  * Empty scrape (zero successful snapshots) skips the commit.
  * All rows in a single run share the same ``as_of`` — the read-side
    "latest per ticker" join needs that to be true.
  * The unique constraint on (ticker, as_of) holds at the row level so
    a scheduler retry within the same second doesn't insert duplicates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from textwrap import dedent

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rmn_dashboard.data.universe import load_universe
from rmn_dashboard.models import TickerQuote
from rmn_dashboard.tasks.ingest_yfinance import run_yfinance_ingest


@pytest.fixture
def small_universe(tmp_path):
    """Two-ticker universe so the assertions can name the rows directly."""
    body = dedent(
        """\
        version: 1
        last_reviewed: 2026-04-24
        tickers:
          - ticker: UVE
            name: Universal Insurance Holdings
            sector: insurer
            key_states: [FL]
            hurricane_relevance: high
          - ticker: NEE
            name: NextEra Energy
            sector: utility
            key_states: [FL]
            hurricane_relevance: high
        """
    )
    path = tmp_path / "small.yaml"
    path.write_text(body, encoding="utf-8")
    load_universe.cache_clear()
    return load_universe(path)


def test_run_persists_one_row_per_snapshot(db_session: Session, small_universe) -> None:
    canned = {
        "UVE": {"last_price": 21.0, "previous_close": 20.0, "last_volume": 100, "currency": "USD"},
        "NEE": {"last_price": 80.0, "previous_close": 81.0, "last_volume": 500, "currency": "USD"},
    }
    count = run_yfinance_ingest(
        db_session,
        universe=small_universe,
        fetch_one=lambda t: canned.get(t),
    )
    assert count == 2

    rows = db_session.query(TickerQuote).order_by(TickerQuote.ticker).all()
    assert [r.ticker for r in rows] == ["NEE", "UVE"]

    uve = next(r for r in rows if r.ticker == "UVE")
    assert uve.last_price == 21.0
    assert uve.prior_close == 20.0
    assert uve.change_amount == pytest.approx(1.0)
    assert uve.change_percent == pytest.approx(5.0)
    assert uve.currency == "USD"
    assert uve.source == "yfinance"


def test_run_skips_commit_when_zero_snapshots(db_session: Session, small_universe) -> None:
    """Every fetch fails → return 0 and don't commit anything."""
    count = run_yfinance_ingest(
        db_session,
        universe=small_universe,
        fetch_one=lambda t: None,
    )
    assert count == 0
    assert db_session.query(TickerQuote).count() == 0


def test_run_stamps_all_rows_with_same_as_of(db_session: Session, small_universe) -> None:
    """A single run's rows must share an ``as_of`` — the service-layer
    "latest per ticker" join needs that consistency.
    """
    canned = {
        "UVE": {"last_price": 21.0, "previous_close": 20.0},
        "NEE": {"last_price": 80.0, "previous_close": 80.0},
    }
    run_yfinance_ingest(
        db_session,
        universe=small_universe,
        fetch_one=lambda t: canned.get(t),
    )
    rows = db_session.query(TickerQuote).all()
    assert len({r.as_of for r in rows}) == 1


def test_unique_constraint_blocks_same_ticker_same_as_of(db_session: Session) -> None:
    """The (ticker, as_of) unique constraint must hold — it's the
    snapshot table's correctness guarantee.
    """
    fixed_now = datetime(2026, 4, 24, 17, 0, tzinfo=UTC)
    db_session.add(TickerQuote(ticker="UVE", last_price=21.0, as_of=fixed_now))
    db_session.commit()

    db_session.add(TickerQuote(ticker="UVE", last_price=22.0, as_of=fixed_now))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
