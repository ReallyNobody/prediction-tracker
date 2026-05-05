"""Prediction-market read helpers — the view-side of the translation layer.

``ingest_kalshi`` writes snapshot rows (one per market, per scrape run). The
panel template needs the *latest* snapshot per market, ranked by how much
money is actually on the line. This module hides that dedup query from
route handlers so they stay focused on request wiring.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rmn_dashboard.models import PredictionMarket


def latest_hurricane_markets(
    db: Session,
    limit: int = 10,
) -> list[PredictionMarket]:
    """Return the most recent snapshot per hurricane market, ordered by
    cumulative trading volume descending (most-traded first).

    Day 37 ordering pivot: previously ranked by open interest, but with
    Polymarket added as a second platform alongside Kalshi we needed a
    metric both platforms expose at the per-market level. Polymarket
    reports OI only on the parent event (we surface it for display, but
    it's nested), whereas ``volume_total`` sits cleanly at the market
    level on both platforms. Volume is also editorially the more
    interesting metric for a journalism dashboard — "how much money
    has actually moved on this question?" reads more directly than
    open contract count.

    Implementation note: group-by subquery → join. Portable across SQLite
    (dev) and Postgres (prod); no need for Postgres-only ``DISTINCT ON`` or
    window functions. Fine up to ~low-thousands of snapshots, which is our
    Week-2/3 data volume.
    """
    # Step 1: for each hurricane market, find its most recent snapshot timestamp.
    latest_per_ticker = (
        select(
            PredictionMarket.platform,
            PredictionMarket.ticker,
            func.max(PredictionMarket.last_updated).label("max_ts"),
        )
        .where(PredictionMarket.category == "hurricane")
        .group_by(PredictionMarket.platform, PredictionMarket.ticker)
        .subquery()
    )

    # Step 2: join back to pull the full row that matches that timestamp.
    stmt = (
        select(PredictionMarket)
        .join(
            latest_per_ticker,
            (PredictionMarket.platform == latest_per_ticker.c.platform)
            & (PredictionMarket.ticker == latest_per_ticker.c.ticker)
            & (PredictionMarket.last_updated == latest_per_ticker.c.max_ts),
        )
        .where(PredictionMarket.category == "hurricane")
        .order_by(
            # NULL volume sorts last so rows with real activity always show
            # first — important pre-season when half the markets have zero
            # volume.
            PredictionMarket.volume_total.desc().nulls_last(),
            PredictionMarket.ticker,
        )
        .limit(limit)
    )

    return list(db.scalars(stmt).all())
