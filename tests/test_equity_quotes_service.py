"""Tests for ``services/equity_quotes.latest_universe_quotes``.

Lock down the contract Panel 2 will rely on:

  * One row per universe ticker, in universe order.
  * Latest snapshot per ticker (when multiple exist).
  * Sector filter narrows correctly.
  * State filter intersects ``key_states`` and never returns reinsurers.
  * Tickers with no quote yet still appear with ``"quote": null`` so a
    scraper outage doesn't drop the roster.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from textwrap import dedent

import pytest
from sqlalchemy.orm import Session

from rmn_dashboard.data.universe import load_universe
from rmn_dashboard.models import TickerQuote
from rmn_dashboard.services.equity_quotes import latest_universe_quotes


@pytest.fixture
def small_universe(tmp_path):
    """Five-ticker universe spanning all four sectors."""
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
          - ticker: LEN
            name: Lennar
            sector: homebuilder
            key_states: [FL, TX]
            hurricane_relevance: high
          - ticker: RNR
            name: RenaissanceRe Holdings
            sector: reinsurer
            key_states: []
            hurricane_relevance: high
          - ticker: ETR
            name: Entergy
            sector: utility
            key_states: [LA, TX]
            hurricane_relevance: high
        """
    )
    path = tmp_path / "small.yaml"
    path.write_text(body, encoding="utf-8")
    load_universe.cache_clear()
    return load_universe(path)


def _add_quote(
    db: Session,
    ticker: str,
    *,
    last_price: float,
    prior_close: float | None = None,
    minutes_ago: int = 0,
) -> None:
    base = datetime(2026, 4, 24, 17, 0, tzinfo=UTC)
    db.add(
        TickerQuote(
            ticker=ticker,
            last_price=last_price,
            prior_close=prior_close,
            change_amount=(last_price - prior_close) if prior_close else None,
            change_percent=(
                ((last_price - prior_close) / prior_close * 100) if prior_close else None
            ),
            currency="USD",
            source="test",
            as_of=base - timedelta(minutes=minutes_ago),
        )
    )


def test_returns_one_row_per_universe_ticker(db_session: Session, small_universe) -> None:
    """Every entry in the universe shows up — even tickers with no quote."""
    _add_quote(db_session, "UVE", last_price=21.0, prior_close=20.0)
    db_session.commit()

    result = latest_universe_quotes(db_session, universe=small_universe)
    tickers = [r["ticker"] for r in result]
    assert tickers == ["UVE", "NEE", "LEN", "RNR", "ETR"]

    by_ticker = {r["ticker"]: r for r in result}
    assert by_ticker["UVE"]["quote"] is not None
    assert by_ticker["UVE"]["quote"]["last_price"] == 21.0
    # No quote yet for the others — the row should still be there.
    for missing in ("NEE", "LEN", "RNR", "ETR"):
        assert by_ticker[missing]["quote"] is None
        # Universe metadata still flows through.
        assert by_ticker[missing]["sector"] in {"insurer", "reinsurer", "homebuilder", "utility"}


def test_returns_only_latest_quote_per_ticker(db_session: Session, small_universe) -> None:
    """Two snapshots for UVE; service returns only the most recent one."""
    _add_quote(db_session, "UVE", last_price=20.0, prior_close=19.5, minutes_ago=120)
    _add_quote(db_session, "UVE", last_price=21.5, prior_close=20.0, minutes_ago=0)
    db_session.commit()

    result = latest_universe_quotes(db_session, universe=small_universe)
    uve = next(r for r in result if r["ticker"] == "UVE")
    assert uve["quote"]["last_price"] == 21.5  # latest, not the older 20.0


def test_sector_filter_narrows(db_session: Session, small_universe) -> None:
    result = latest_universe_quotes(db_session, sectors=["utility"], universe=small_universe)
    tickers = {r["ticker"] for r in result}
    assert tickers == {"NEE", "ETR"}


def test_sector_filter_multi(db_session: Session, small_universe) -> None:
    result = latest_universe_quotes(
        db_session, sectors=["insurer", "homebuilder"], universe=small_universe
    )
    tickers = {r["ticker"] for r in result}
    assert tickers == {"UVE", "LEN"}


def test_state_filter_returns_intersecting_tickers(db_session: Session, small_universe) -> None:
    """FL cone: UVE + NEE + LEN. Reinsurer RNR never lights up; ETR is LA/TX."""
    result = latest_universe_quotes(db_session, states=["FL"], universe=small_universe)
    tickers = {r["ticker"] for r in result}
    assert tickers == {"UVE", "NEE", "LEN"}
    assert "RNR" not in tickers
    assert "ETR" not in tickers


def test_state_filter_combined_with_sector_filter(db_session: Session, small_universe) -> None:
    """Cone + filter pill: FL utilities only → NEE."""
    result = latest_universe_quotes(
        db_session, sectors=["utility"], states=["FL"], universe=small_universe
    )
    tickers = {r["ticker"] for r in result}
    assert tickers == {"NEE"}


def test_quote_payload_shape(db_session: Session, small_universe) -> None:
    """Verify the JSON-serializable shape of the quote sub-dict.

    Panel 2's JS targets these keys directly; if any of them rename or
    disappear, the UI silently breaks rather than throwing.
    """
    _add_quote(db_session, "UVE", last_price=21.45, prior_close=20.0)
    db_session.commit()

    result = latest_universe_quotes(db_session, universe=small_universe)
    uve = next(r for r in result if r["ticker"] == "UVE")
    quote = uve["quote"]
    assert set(quote.keys()) == {
        "last_price",
        "prior_close",
        "change_amount",
        "change_percent",
        "currency",
        "volume",
        "market_cap",
        "source",
        "as_of",
    }
    assert isinstance(quote["as_of"], str)  # ISO-8601 not raw datetime
