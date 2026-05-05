"""Polymarket ingestion task — fetch hurricane markets and persist as snapshots.

Mirrors the shape of ``ingest_kalshi.py``: each call appends one fresh snapshot
row per market to ``prediction_markets`` (the table is snapshot-shaped, not
upsert-shaped — the unique constraint is on
``(platform, ticker, last_updated)``). Read-side queries deduplicate back to
"latest per ticker" via ``services/markets.latest_hurricane_markets``.

Runnable two ways:

    # From Python / APScheduler:
    from rmn_dashboard.tasks.ingest_polymarket import run_polymarket_ingest
    run_polymarket_ingest(db_session)

    # From the CLI (Render Shell, local):
    python -m rmn_dashboard.tasks.ingest_polymarket

The CLI wrapper builds its own session and closes it; the library function
takes a session so callers (tests, scheduler) can control lifecycle.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from rmn_dashboard.models import PredictionMarket
from rmn_dashboard.scrapers.polymarket import fetch_hurricane_markets

if TYPE_CHECKING:
    from rmn_dashboard.scrapers.polymarket import PolymarketClient, PolymarketMarket

logger = logging.getLogger(__name__)


def _to_close_date(close_time: str | None) -> datetime.date | None:
    """Parse Polymarket's ISO-8601 endDate into a date.

    Polymarket returns strings like ``"2026-12-31T00:00:00Z"`` (most
    common) or ``"2026-12-31"`` for the ``endDateIso`` variant the
    scraper falls back to. ``datetime.fromisoformat`` handles both
    after the ``Z`` substitution; malformed values fall back to None
    rather than aborting the whole batch.
    """
    if not close_time:
        return None
    try:
        return datetime.fromisoformat(close_time.replace("Z", "+00:00")).date()
    except ValueError:
        logger.warning("Unparseable Polymarket close_time=%r", close_time)
        return None


def _market_to_row(pm: PolymarketMarket) -> PredictionMarket:
    """Map a normalized ``PolymarketMarket`` onto a ``PredictionMarket`` row.

    ``last_updated`` is set by the DB default (``func.now()``) so every
    snapshot in this batch shares a consistent server-clock timestamp.
    """
    return PredictionMarket(
        platform="polymarket",
        ticker=pm.ticker,
        event_ticker=pm.event_ticker,
        title=pm.title,
        category="hurricane",
        yes_price=pm.yes_price,
        no_price=pm.no_price,
        volume_24h=pm.volume_24h,
        volume_total=pm.volume_total,
        open_interest=pm.open_interest,
        close_date=_to_close_date(pm.close_time),
    )


def run_polymarket_ingest(
    db: Session,
    client: PolymarketClient | None = None,
) -> int:
    """Fetch open Polymarket hurricane markets and persist one snapshot per
    market. Returns the count inserted.

    Transaction shape: single commit at the end. A partial scrape (some
    markets unparseable, some pages 4xx'd) still persists what it got —
    the per-page ``try/except`` inside ``fetch_hurricane_markets`` means
    upstream failures log-and-continue.

    Off-season behavior: zero hurricane markets is normal during winter.
    The function returns 0 and skips the commit; the scheduler logs
    "Scheduled Polymarket ingest persisted 0 rows" and moves on.
    """
    markets = fetch_hurricane_markets(client=client)
    if not markets:
        logger.info("Polymarket ingest produced zero hurricane markets — skipping commit.")
        return 0

    rows = [_market_to_row(m) for m in markets if m.ticker]
    db.add_all(rows)
    db.commit()
    logger.info("Polymarket ingest persisted %d snapshot rows.", len(rows))
    return len(rows)


def _cli() -> int:
    """Stand-alone entry point — builds its own session and logging config."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from rmn_dashboard.database import SessionLocal

    db = SessionLocal()
    try:
        count = run_polymarket_ingest(db)
        print(f"Persisted {count} Polymarket market snapshots.")
        return 0 if count >= 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(_cli())
