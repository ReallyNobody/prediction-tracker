"""Unit tests for rmn_dashboard.services.markets.

Seeded directly against the ``prediction_markets`` table so the tests exercise
exactly the SQL the production route hits. The ``db_session`` fixture gives
us a fresh in-memory SQLite with the full schema each time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from rmn_dashboard.models import PredictionMarket
from rmn_dashboard.services.markets import latest_hurricane_markets


def _snapshot(
    ticker: str,
    *,
    yes_price: float,
    open_interest: float | None,
    last_updated: datetime,
    category: str = "hurricane",
    platform: str = "kalshi",
    title: str | None = None,
) -> PredictionMarket:
    """Build a PredictionMarket row with explicit last_updated (bypasses
    server-default) so we can seed deterministic timelines in tests."""
    return PredictionMarket(
        platform=platform,
        ticker=ticker,
        event_ticker=ticker.split("-", 1)[0] if "-" in ticker else None,
        title=title or f"Market {ticker}",
        category=category,
        yes_price=yes_price,
        no_price=max(0.0, 1.0 - yes_price),
        open_interest=open_interest,
        last_updated=last_updated,
    )


def test_latest_hurricane_markets_returns_empty_when_no_rows(db_session: Session) -> None:
    assert latest_hurricane_markets(db_session) == []


def test_latest_hurricane_markets_picks_most_recent_snapshot_per_ticker(
    db_session: Session,
) -> None:
    """Two snapshots of the same ticker at different times → only the newer
    one should come back. Prices differ so we can prove which row won."""
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    older = _snapshot(
        "KXHURCTOT-26DEC01-T7",
        yes_price=0.30,
        open_interest=100.0,
        last_updated=now - timedelta(hours=6),
    )
    newer = _snapshot(
        "KXHURCTOT-26DEC01-T7",
        yes_price=0.50,
        open_interest=250.0,
        last_updated=now,
    )
    db_session.add_all([older, newer])
    db_session.commit()

    rows = latest_hurricane_markets(db_session)
    assert len(rows) == 1
    assert rows[0].yes_price == 0.50
    assert rows[0].open_interest == 250.0


def test_latest_hurricane_markets_excludes_non_hurricane_categories(
    db_session: Session,
) -> None:
    """Only category='hurricane' rows should appear — the panel is
    hurricane-only, even though the table may hold wildfire/quake markets
    later."""
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    db_session.add_all(
        [
            _snapshot("KXHURCTOT-T7", yes_price=0.5, open_interest=100.0, last_updated=now),
            _snapshot(
                "KXFIRE-CA",
                yes_price=0.2,
                open_interest=999.0,
                last_updated=now,
                category="wildfire",
            ),
        ]
    )
    db_session.commit()

    rows = latest_hurricane_markets(db_session)
    assert [r.ticker for r in rows] == ["KXHURCTOT-T7"]


def test_latest_hurricane_markets_orders_by_open_interest_desc_nulls_last(
    db_session: Session,
) -> None:
    """High OI first, zero/low next, NULL last. Ticker is tiebreaker so the
    order is deterministic when two markets share OI."""
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    db_session.add_all(
        [
            _snapshot("M-HIGH", yes_price=0.5, open_interest=500.0, last_updated=now),
            _snapshot("M-NULL", yes_price=0.5, open_interest=None, last_updated=now),
            _snapshot("M-LOW", yes_price=0.5, open_interest=10.0, last_updated=now),
        ]
    )
    db_session.commit()

    rows = latest_hurricane_markets(db_session)
    assert [r.ticker for r in rows] == ["M-HIGH", "M-LOW", "M-NULL"]


def test_latest_hurricane_markets_respects_limit(db_session: Session) -> None:
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    db_session.add_all(
        [
            _snapshot(f"T{i}", yes_price=0.5, open_interest=float(i), last_updated=now)
            for i in range(15)
        ]
    )
    db_session.commit()

    rows = latest_hurricane_markets(db_session, limit=5)
    assert len(rows) == 5
    # Top 5 by OI descending: 14, 13, 12, 11, 10
    assert [r.ticker for r in rows] == ["T14", "T13", "T12", "T11", "T10"]


def test_latest_hurricane_markets_handles_multiple_tickers_with_mixed_histories(
    db_session: Session,
) -> None:
    """Realistic shape: several markets, each with 2–3 snapshot rows at
    different timestamps. Service should dedup to one row per ticker and
    order them by the latest snapshot's open_interest."""
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    db_session.add_all(
        [
            # Market A: older=50 OI, newer=300 OI → latest wins
            _snapshot(
                "A", yes_price=0.4, open_interest=50.0, last_updated=now - timedelta(hours=3)
            ),
            _snapshot("A", yes_price=0.5, open_interest=300.0, last_updated=now),
            # Market B: single snapshot with big OI
            _snapshot("B", yes_price=0.3, open_interest=500.0, last_updated=now),
            # Market C: two snapshots, latest has smaller OI than first
            _snapshot(
                "C", yes_price=0.7, open_interest=999.0, last_updated=now - timedelta(hours=2)
            ),
            _snapshot("C", yes_price=0.6, open_interest=10.0, last_updated=now),
        ]
    )
    db_session.commit()

    rows = latest_hurricane_markets(db_session)
    # Ordered by latest-snapshot OI desc: B(500), A(300), C(10).
    assert [r.ticker for r in rows] == ["B", "A", "C"]
    # And the values come from the latest snapshot, not the first.
    assert next(r for r in rows if r.ticker == "A").open_interest == 300.0
    assert next(r for r in rows if r.ticker == "C").open_interest == 10.0
