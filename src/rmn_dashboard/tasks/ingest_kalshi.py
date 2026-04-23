"""Kalshi ingestion task — fetch hurricane markets and persist as snapshots.

Each call appends a fresh snapshot row per market to ``prediction_markets``.
The table is snapshot-shaped (``UniqueConstraint("platform", "ticker",
"last_updated")``) so repeat runs don't need to upsert; they just insert.
Read-side queries deduplicate back to "latest per ticker" when rendering.

Runnable two ways:

    # From Python / APScheduler:
    from rmn_dashboard.tasks.ingest_kalshi import run_kalshi_ingest
    run_kalshi_ingest(db_session)

    # From the CLI (Render Shell, local):
    python -m rmn_dashboard.tasks.ingest_kalshi

The CLI wrapper builds its own session and closes it; the library function
takes a session so callers (tests, scheduler) can control lifecycle.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from rmn_dashboard.models import PredictionMarket
from rmn_dashboard.scrapers.kalshi import HURRICANE_SERIES, fetch_hurricane_markets

if TYPE_CHECKING:
    from rmn_dashboard.scrapers.kalshi import KalshiClient, KalshiMarket

logger = logging.getLogger(__name__)


def _to_close_date(close_time: str | None) -> datetime | None:
    """Parse Kalshi's ISO-8601 close_time string into a date (or None).

    Kalshi returns strings like ``"2026-12-01T00:00:00Z"``. We only care about
    the date portion for display; dropping tz info is deliberate — the column
    is ``Date``, not ``DateTime``. Malformed values fall back to None rather
    than aborting the whole batch.
    """
    if not close_time:
        return None
    try:
        # "Z" suffix isn't accepted by fromisoformat before 3.11; replace
        # proactively so older Pythons still parse.
        return datetime.fromisoformat(close_time.replace("Z", "+00:00")).date()
    except ValueError:
        logger.warning("Unparseable Kalshi close_time=%r", close_time)
        return None


def _market_to_row(km: KalshiMarket) -> PredictionMarket:
    """Map a normalized ``KalshiMarket`` dataclass onto a ``PredictionMarket``
    snapshot row. ``last_updated`` is set by the DB default (``func.now()``)
    so every snapshot shares a consistent server-clock timestamp."""
    return PredictionMarket(
        platform="kalshi",
        ticker=km.ticker or "",
        event_ticker=km.event_ticker,
        title=km.title or "",
        category="hurricane",
        # Kalshi returns 5 price points (yes/no bid & ask, plus last_price).
        # The PredictionMarket schema only has yes_price/no_price today, so we
        # use last_price (the actual last traded price) and derive the no side
        # as its complement. Full bid/ask spread capture is Week 4 work.
        yes_price=km.last_price,
        no_price=max(0.0, 1.0 - km.last_price) if km.last_price else None,
        volume_24h=km.volume_24h,
        volume_total=km.volume_total,
        open_interest=km.open_interest,
        close_date=_to_close_date(km.close_time),
    )


def run_kalshi_ingest(
    db: Session,
    series_tickers: Iterable[str] = HURRICANE_SERIES,
    client: KalshiClient | None = None,
) -> int:
    """Fetch configured Kalshi hurricane markets and persist one snapshot per
    market. Returns the count inserted.

    Transaction shape: single commit at the end. A partial scrape that
    returns 17 markets instead of 25 still persists what it got — the
    per-series ``try/except`` inside ``fetch_hurricane_markets`` means
    upstream failures log-and-continue, and we treat whatever comes back as
    authoritative for this run.
    """
    markets = fetch_hurricane_markets(series_tickers, client=client)
    if not markets:
        logger.warning("Kalshi ingest produced zero markets — skipping commit.")
        return 0

    rows = [_market_to_row(m) for m in markets if m.ticker]  # skip any null-ticker
    db.add_all(rows)
    db.commit()
    logger.info("Kalshi ingest persisted %d snapshot rows.", len(rows))
    return len(rows)


def _cli() -> int:
    """Stand-alone entry point — builds its own session and logging config."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from rmn_dashboard.database import SessionLocal

    db = SessionLocal()
    try:
        count = run_kalshi_ingest(db)
        print(f"Persisted {count} Kalshi market snapshots.")
        return 0 if count > 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(_cli())
