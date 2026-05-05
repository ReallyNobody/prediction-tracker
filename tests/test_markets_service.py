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
    last_updated: datetime,
    open_interest: float | None = None,
    volume_total: float | None = None,
    volume_24h: float | None = None,
    category: str = "hurricane",
    platform: str = "kalshi",
    title: str | None = None,
) -> PredictionMarket:
    """Build a PredictionMarket row with explicit last_updated (bypasses
    server-default) so we can seed deterministic timelines in tests.

    Day 37: ordering switched from ``open_interest`` to ``volume_total``
    when Polymarket joined Kalshi in Panel 4. Both fields stay on the
    model and are independently settable from this helper so individual
    tests can target whichever field they're exercising. ``open_interest``
    and ``volume_24h`` default to None for tests that don't care about
    them.
    """
    return PredictionMarket(
        platform=platform,
        ticker=ticker,
        event_ticker=ticker.split("-", 1)[0] if "-" in ticker else None,
        title=title or f"Market {ticker}",
        category=category,
        yes_price=yes_price,
        no_price=max(0.0, 1.0 - yes_price),
        open_interest=open_interest,
        volume_total=volume_total,
        volume_24h=volume_24h,
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
        volume_total=10000.0,
        last_updated=now - timedelta(hours=6),
    )
    newer = _snapshot(
        "KXHURCTOT-26DEC01-T7",
        yes_price=0.50,
        volume_total=25000.0,
        last_updated=now,
    )
    db_session.add_all([older, newer])
    db_session.commit()

    rows = latest_hurricane_markets(db_session)
    assert len(rows) == 1
    assert rows[0].yes_price == 0.50
    assert rows[0].volume_total == 25000.0


def test_latest_hurricane_markets_excludes_non_hurricane_categories(
    db_session: Session,
) -> None:
    """Only category='hurricane' rows should appear — the panel is
    hurricane-only, even though the table may hold wildfire/quake markets
    later."""
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    db_session.add_all(
        [
            _snapshot("KXHURCTOT-T7", yes_price=0.5, volume_total=10000.0, last_updated=now),
            _snapshot(
                "KXFIRE-CA",
                yes_price=0.2,
                volume_total=99999.0,
                last_updated=now,
                category="wildfire",
            ),
        ]
    )
    db_session.commit()

    rows = latest_hurricane_markets(db_session)
    assert [r.ticker for r in rows] == ["KXHURCTOT-T7"]


def test_latest_hurricane_markets_orders_by_volume_total_desc_nulls_last(
    db_session: Session,
) -> None:
    """Day 37 ordering pivot: rows are ranked by ``volume_total``
    descending — most-traded first — with NULLs sorting last and the
    ticker name as a deterministic tiebreaker. Previously ranked by
    open_interest; switched when Polymarket joined Kalshi because
    Polymarket only exposes OI at the parent-event level whereas
    volume_total is per-market on both platforms."""
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    db_session.add_all(
        [
            _snapshot("M-HIGH", yes_price=0.5, volume_total=50000.0, last_updated=now),
            _snapshot("M-NULL", yes_price=0.5, volume_total=None, last_updated=now),
            _snapshot("M-LOW", yes_price=0.5, volume_total=1000.0, last_updated=now),
        ]
    )
    db_session.commit()

    rows = latest_hurricane_markets(db_session)
    assert [r.ticker for r in rows] == ["M-HIGH", "M-LOW", "M-NULL"]


def test_latest_hurricane_markets_respects_limit(db_session: Session) -> None:
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    db_session.add_all(
        [
            _snapshot(
                f"T{i:02d}",  # zero-padded so name-sort matches integer order
                yes_price=0.5,
                volume_total=float(i * 1000),
                last_updated=now,
            )
            for i in range(15)
        ]
    )
    db_session.commit()

    rows = latest_hurricane_markets(db_session, limit=5)
    assert len(rows) == 5
    # Top 5 by volume_total descending: T14, T13, T12, T11, T10.
    assert [r.ticker for r in rows] == ["T14", "T13", "T12", "T11", "T10"]


def test_latest_hurricane_markets_handles_multiple_tickers_with_mixed_histories(
    db_session: Session,
) -> None:
    """Realistic shape: several markets, each with 2–3 snapshot rows at
    different timestamps. Service should dedup to one row per ticker and
    order them by the latest snapshot's volume_total."""
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    db_session.add_all(
        [
            # Market A: older=$5K, newer=$30K vol → latest wins
            _snapshot(
                "A",
                yes_price=0.4,
                volume_total=5000.0,
                last_updated=now - timedelta(hours=3),
            ),
            _snapshot("A", yes_price=0.5, volume_total=30000.0, last_updated=now),
            # Market B: single snapshot with big volume
            _snapshot("B", yes_price=0.3, volume_total=50000.0, last_updated=now),
            # Market C: two snapshots, latest has smaller volume than first
            _snapshot(
                "C",
                yes_price=0.7,
                volume_total=99000.0,
                last_updated=now - timedelta(hours=2),
            ),
            _snapshot("C", yes_price=0.6, volume_total=1000.0, last_updated=now),
        ]
    )
    db_session.commit()

    rows = latest_hurricane_markets(db_session)
    # Ordered by latest-snapshot volume desc: B(50K), A(30K), C(1K).
    assert [r.ticker for r in rows] == ["B", "A", "C"]
    # And the values come from the latest snapshot, not the first.
    assert next(r for r in rows if r.ticker == "A").volume_total == 30000.0
    assert next(r for r in rows if r.ticker == "C").volume_total == 1000.0
