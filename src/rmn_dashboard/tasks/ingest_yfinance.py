"""yfinance ingest task — pull universe quotes and persist as snapshots.

Each call appends one row per successfully-fetched ticker to
``ticker_quotes``. The table is snapshot-shaped (``UniqueConstraint(
"ticker", "as_of")``) so repeat runs don't need upserts; they just
insert.

Two callable shapes, parallel to ``ingest_kalshi``:

    # From Python / APScheduler:
    from rmn_dashboard.tasks.ingest_yfinance import run_yfinance_ingest
    run_yfinance_ingest(db_session)

    # From the CLI (Render Shell, local):
    python -m rmn_dashboard.tasks.ingest_yfinance

The CLI wrapper builds and closes its own session; the library
function takes a session so callers (tests, scheduler) own lifecycle.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from rmn_dashboard.data.universe import Universe
from rmn_dashboard.models import TickerQuote
from rmn_dashboard.scrapers.yfinance_quotes import (
    QuoteFetcher,
    QuoteSnapshot,
    fetch_universe_quotes,
)

logger = logging.getLogger(__name__)


def _snapshot_to_row(snap: QuoteSnapshot) -> TickerQuote:
    """Map a ``QuoteSnapshot`` dataclass onto a ``TickerQuote`` row.

    ``as_of`` is set explicitly from the snapshot's ``fetched_at`` (not
    the DB ``server_default``) so every row in a single scrape shares
    the same timestamp — the read-side service joins on ``MAX(as_of)``
    per ticker, and a ragged batch (each row stamped at insert time)
    would produce inconsistent "latest" sets.
    """
    return TickerQuote(
        ticker=snap.ticker,
        last_price=snap.last_price,
        prior_close=snap.prior_close,
        change_amount=snap.change_amount,
        change_percent=snap.change_percent,
        volume=snap.volume,
        market_cap=snap.market_cap,
        currency=snap.currency,
        source=snap.source,
        as_of=snap.fetched_at,
    )


def run_yfinance_ingest(
    db: Session,
    *,
    universe: Universe | None = None,
    fetch_one: QuoteFetcher | None = None,
) -> int:
    """Fetch universe quotes and persist one snapshot per ticker. Return count.

    Transaction shape: single commit at the end. A partial scrape that
    returns 28 of 35 tickers still persists what it got — the
    log-and-skip pattern in the scraper means upstream failures don't
    abort the whole run.
    """
    snapshots = fetch_universe_quotes(universe=universe, fetch_one=fetch_one)
    if not snapshots:
        logger.warning("yfinance ingest produced zero snapshots — skipping commit.")
        return 0

    rows = [_snapshot_to_row(s) for s in snapshots]
    db.add_all(rows)
    db.commit()
    logger.info("yfinance ingest persisted %d snapshot rows.", len(rows))
    return len(rows)


def _cli() -> int:
    """Stand-alone entry point — builds its own session and logging config."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from rmn_dashboard.database import SessionLocal

    db = SessionLocal()
    try:
        count = run_yfinance_ingest(db)
        print(f"Persisted {count} ticker_quotes snapshot rows.")
        return 0 if count > 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(_cli())
