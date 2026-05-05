"""Unit tests for ``rmn_dashboard.tasks.ingest_polymarket``.

Day 37 — thin glue layer between the Polymarket scraper and the
PredictionMarket table. The scraper itself is tested separately in
``test_polymarket.py``; here we cover the ingest task's behavior:
field mapping from PolymarketMarket → PredictionMarket, transaction
shape (single commit at end), and off-season behavior (zero markets
should not commit anything).
"""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy.orm import Session

from rmn_dashboard.models import PredictionMarket
from rmn_dashboard.scrapers.polymarket import PolymarketMarket
from rmn_dashboard.tasks.ingest_polymarket import (
    _market_to_row,
    _to_close_date,
    run_polymarket_ingest,
)


# A canonical PolymarketMarket built from the Day 36 probe data — used as
# the input to the field-mapping tests so we don't have to reconstruct
# the shape in each test.
_FIXTURE_MARKET = PolymarketMarket(
    platform="polymarket",
    ticker="will-a-hurricane-make-landfall-in-the-us-by-may-31",
    event_ticker="will-a-hurricane-make-landfall-in-the-us-by-may-31",
    title="Will a hurricane make landfall in the US by May 31?",
    yes_price=0.0195,
    no_price=0.9805,
    volume_24h=61.02,
    volume_total=17317.78,
    open_interest=2770.31,
    close_time="2026-05-31T00:00:00Z",
    url=(
        "https://polymarket.com/event/will-a-hurricane-make-landfall-in-the-us-by-may-31"
    ),
)


def test_market_to_row_maps_required_fields() -> None:
    """Spot-check the PolymarketMarket → PredictionMarket field mapping.
    platform is hardcoded to "polymarket"; ticker comes from the slug;
    title from the question; volume_total / volume_24h / open_interest
    pass through; close_time parses to a date.
    """
    row = _market_to_row(_FIXTURE_MARKET)

    assert row.platform == "polymarket"
    assert row.ticker == "will-a-hurricane-make-landfall-in-the-us-by-may-31"
    assert row.title == "Will a hurricane make landfall in the US by May 31?"
    assert row.category == "hurricane"
    assert row.yes_price == 0.0195
    assert row.no_price == 0.9805
    assert row.volume_total == 17317.78
    assert row.volume_24h == 61.02
    assert row.open_interest == 2770.31


def test_market_to_row_parses_close_time_to_date() -> None:
    """Polymarket's endDate is ISO-8601; the model stores a Date.
    The conversion drops the time portion and ignores tz."""
    row = _market_to_row(_FIXTURE_MARKET)

    assert row.close_date is not None
    assert row.close_date.isoformat() == "2026-05-31"


def test_to_close_date_handles_missing_or_malformed_input() -> None:
    """None / empty / unparseable values fall back to None rather than
    crashing the batch."""
    assert _to_close_date(None) is None
    assert _to_close_date("") is None
    assert _to_close_date("not-a-date") is None
    # Real ISO-8601 still parses.
    assert _to_close_date("2026-05-31T00:00:00Z").isoformat() == "2026-05-31"
    # ``endDateIso`` form (date only) also parses.
    assert _to_close_date("2026-05-31").isoformat() == "2026-05-31"


def test_run_polymarket_ingest_persists_snapshots(db_session: Session) -> None:
    """End-to-end: patch fetch_hurricane_markets to return our fixture,
    confirm one row lands in the table with the expected values."""
    with patch(
        "rmn_dashboard.tasks.ingest_polymarket.fetch_hurricane_markets",
        return_value=[_FIXTURE_MARKET],
    ):
        count = run_polymarket_ingest(db_session)

    assert count == 1

    rows = db_session.query(PredictionMarket).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.platform == "polymarket"
    assert row.ticker == "will-a-hurricane-make-landfall-in-the-us-by-may-31"
    assert row.volume_total == 17317.78


def test_run_polymarket_ingest_skips_commit_when_no_markets(db_session: Session) -> None:
    """Off-season behavior: zero hurricane markets means nothing to
    persist. The function returns 0 and the commit is skipped (so a
    later transaction in the same session isn't accidentally rolled
    into the empty one)."""
    with patch(
        "rmn_dashboard.tasks.ingest_polymarket.fetch_hurricane_markets",
        return_value=[],
    ):
        count = run_polymarket_ingest(db_session)

    assert count == 0
    assert db_session.query(PredictionMarket).count() == 0


def test_run_polymarket_ingest_persists_multiple_rows(db_session: Session) -> None:
    """Realistic shape: 5 hurricane markets in one batch (matches the
    Day 36 probe's hit count). All persist as a single commit."""
    fixtures = [
        PolymarketMarket(
            platform="polymarket",
            ticker=f"market-{i}",
            event_ticker=f"market-{i}",
            title=f"Hurricane question {i}?",
            yes_price=0.5,
            no_price=0.5,
            volume_24h=100.0,
            volume_total=float(i * 10000),
            open_interest=None,
            close_time="2026-12-31T00:00:00Z",
            url=f"https://polymarket.com/event/market-{i}",
        )
        for i in range(5)
    ]

    with patch(
        "rmn_dashboard.tasks.ingest_polymarket.fetch_hurricane_markets",
        return_value=fixtures,
    ):
        count = run_polymarket_ingest(db_session)

    assert count == 5
    assert db_session.query(PredictionMarket).count() == 5
