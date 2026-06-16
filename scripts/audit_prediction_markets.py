#!/usr/bin/env python3
"""Audit what's actually in the ``prediction_markets`` table.

Editorial reconnaissance script — answers "what are we capturing right
now, what gaps exist, and where should we spend the next editorial
hour" without touching production data. Read-only queries; safe to run
against prod.

Run locally with prod DATABASE_URL (paste from Render's environment
settings):

    DATABASE_URL=postgres://... python scripts/audit_prediction_markets.py

Or against the local SQLite dev DB:

    python scripts/audit_prediction_markets.py

Output: Markdown to stdout. Pipe to a file or paste into a working
doc. Sections:

  1. Per-platform totals — what scrapers are producing
  2. Top hurricane-category tickers by volume — what's actually
     traded enough to read editorially
  3. Heat-map YAML cross-reference — which canonical questions point
     at tickers we don't have (silent failure: typo or stale), and
     which DB tickers aren't yet in any canonical question (Session 4
     candidates)
  4. Freshness signal — tickers whose latest snapshot is stale, which
     usually means the scraper stopped picking them up
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rmn_dashboard.data.heat_map import load_heat_map_questions
from rmn_dashboard.database import SessionLocal
from rmn_dashboard.models import PredictionMarket

# Editorial cutoffs — what counts as "stale" and what counts as a
# meaningful-volume row. Tuned to surface the editorial signal without
# burying the report in noise.
_STALE_CUTOFF_HOURS = 48
_TOP_TICKER_LIMIT = 20


def run() -> None:
    """Build and print the audit report to stdout."""
    db = SessionLocal()
    try:
        print(_format_header())
        print(_section_platform_totals(db))
        print(_section_top_tickers(db))
        print(_section_yaml_cross_reference(db))
        print(_section_freshness(db))
    finally:
        db.close()


# ---- Section 1: Per-platform totals --------------------------------------


def _section_platform_totals(db: Session) -> str:
    """Per-platform: distinct tickers, total snapshots, time range."""
    stmt = (
        select(
            PredictionMarket.platform,
            func.count(func.distinct(PredictionMarket.ticker)).label("ticker_count"),
            func.count().label("snapshot_count"),
            func.min(PredictionMarket.last_updated).label("earliest"),
            func.max(PredictionMarket.last_updated).label("latest"),
        )
        .group_by(PredictionMarket.platform)
        .order_by(PredictionMarket.platform)
    )
    rows = list(db.execute(stmt).all())

    out = ["## 1. Per-platform totals\n"]
    if not rows:
        out.append("_No prediction-market rows in DB._\n")
        return "\n".join(out)

    out.append("| Platform | Tickers | Snapshots | Earliest | Latest |")
    out.append("|---|---:|---:|---|---|")
    for r in rows:
        out.append(
            f"| {r.platform} | {r.ticker_count} | {r.snapshot_count} | "
            f"{_fmt_ts(r.earliest)} | {_fmt_ts(r.latest)} |"
        )
    return "\n".join(out) + "\n"


# ---- Section 2: Top hurricane tickers by volume --------------------------


def _section_top_tickers(db: Session) -> str:
    """Top hurricane-category tickers ranked by latest volume_total.

    Same group-by → join pattern as services/markets.py so the
    snapshot we look at is the most recent one per (platform, ticker).
    """
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
    stmt = (
        select(PredictionMarket)
        .join(
            latest_per_ticker,
            (PredictionMarket.platform == latest_per_ticker.c.platform)
            & (PredictionMarket.ticker == latest_per_ticker.c.ticker)
            & (PredictionMarket.last_updated == latest_per_ticker.c.max_ts),
        )
        .order_by(
            PredictionMarket.volume_total.desc().nulls_last(),
            PredictionMarket.ticker,
        )
        .limit(_TOP_TICKER_LIMIT)
    )
    rows = list(db.scalars(stmt).all())

    out = [f"## 2. Top {_TOP_TICKER_LIMIT} hurricane tickers by latest volume\n"]
    if not rows:
        out.append("_No hurricane-category rows in DB._\n")
        return "\n".join(out)

    out.append("| Platform | Ticker | Title | Yes ¢ | Volume | Close | Last updated |")
    out.append("|---|---|---|---:|---:|---|---|")
    for r in rows:
        out.append(
            f"| {r.platform} | `{r.ticker}` | {_truncate(r.title, 50)} | "
            f"{_fmt_price(r.yes_price)} | {_fmt_volume(r.volume_total)} | "
            f"{r.close_date or '—'} | {_fmt_ts(r.last_updated)} |"
        )
    return "\n".join(out) + "\n"


# ---- Section 3: Heat-map YAML cross-reference ----------------------------


def _section_yaml_cross_reference(db: Session) -> str:
    """Editorial sanity check: do YAML-declared tickers actually have
    DB rows? Are there high-volume DB tickers we haven't curated yet?

    Two failure modes get caught:
      * YAML names a ticker (typo or stale) → heat-map renders the
        cell as "no_recent_snapshot" silently. We surface those here.
      * High-volume hurricane ticker isn't in any canonical question
        → Session 4 candidate. We list the top unclaimed tickers.
    """
    doc = load_heat_map_questions()
    yaml_pairs: set[tuple[str, str]] = set()
    for q in doc.questions:
        for platform, ticker in q.platforms.items():
            yaml_pairs.add((platform, ticker))

    db_pairs = _all_hurricane_pairs(db)

    yaml_only = yaml_pairs - db_pairs  # editorial typos / stale tickers
    db_only = db_pairs - yaml_pairs  # Session 4 candidates

    out = ["## 3. Heat-map YAML ↔ DB cross-reference\n"]

    out.append("### 3a. YAML tickers with no matching DB row")
    if not yaml_only:
        out.append("_All YAML-declared tickers have matching DB rows._\n")
    else:
        out.append("These canonical questions point at tickers the DB doesn't have. ")
        out.append("Likely causes: editorial typo, ticker renamed upstream, or ")
        out.append("scraper hasn't reached the market yet.\n")
        out.append("| Platform | Ticker |")
        out.append("|---|---|")
        for platform, ticker in sorted(yaml_only):
            out.append(f"| {platform} | `{ticker}` |")
        out.append("")

    out.append("### 3b. Top unclaimed hurricane tickers (Session 4 candidates)")
    candidates = _top_unclaimed_tickers(db, db_only)
    if not candidates:
        out.append("_Every hurricane ticker in the DB is already in the YAML._\n")
    else:
        out.append("Ranked by latest volume. Worth considering for canonical questions:\n")
        out.append("| Platform | Ticker | Title | Volume | Yes ¢ |")
        out.append("|---|---|---|---:|---:|")
        for r in candidates:
            out.append(
                f"| {r.platform} | `{r.ticker}` | {_truncate(r.title, 50)} | "
                f"{_fmt_volume(r.volume_total)} | {_fmt_price(r.yes_price)} |"
            )
        out.append("")

    return "\n".join(out)


def _all_hurricane_pairs(db: Session) -> set[tuple[str, str]]:
    stmt = (
        select(PredictionMarket.platform, PredictionMarket.ticker)
        .where(PredictionMarket.category == "hurricane")
        .distinct()
    )
    return {(p, t) for p, t in db.execute(stmt).all()}


def _top_unclaimed_tickers(db: Session, pairs: set[tuple[str, str]]) -> list[PredictionMarket]:
    """Latest snapshot for each (platform, ticker) in the unclaimed
    set, sorted by volume descending. Limited to 15 to keep the report
    scannable."""
    if not pairs:
        return []
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
    stmt = (
        select(PredictionMarket)
        .join(
            latest_per_ticker,
            (PredictionMarket.platform == latest_per_ticker.c.platform)
            & (PredictionMarket.ticker == latest_per_ticker.c.ticker)
            & (PredictionMarket.last_updated == latest_per_ticker.c.max_ts),
        )
        .order_by(
            PredictionMarket.volume_total.desc().nulls_last(),
            PredictionMarket.ticker,
        )
    )
    rows = list(db.scalars(stmt).all())
    return [r for r in rows if (r.platform, r.ticker) in pairs][:15]


# ---- Section 4: Freshness signal -----------------------------------------


def _section_freshness(db: Session) -> str:
    """Hurricane tickers whose latest snapshot is more than
    ``_STALE_CUTOFF_HOURS`` old. Usually means the scraper stopped
    picking them up — market closed, renamed, or fell off the
    category filter."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=_STALE_CUTOFF_HOURS)
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
    stmt = (
        select(
            PredictionMarket.platform,
            PredictionMarket.ticker,
            PredictionMarket.title,
            latest_per_ticker.c.max_ts.label("latest"),
        )
        .join(
            latest_per_ticker,
            (PredictionMarket.platform == latest_per_ticker.c.platform)
            & (PredictionMarket.ticker == latest_per_ticker.c.ticker)
            & (PredictionMarket.last_updated == latest_per_ticker.c.max_ts),
        )
        .where(latest_per_ticker.c.max_ts < cutoff)
        .order_by(latest_per_ticker.c.max_ts)
    )
    rows = list(db.execute(stmt).all())

    out = [f"## 4. Stale tickers (latest snapshot > {_STALE_CUTOFF_HOURS}h old)\n"]
    if not rows:
        out.append(f"_All hurricane tickers updated within {_STALE_CUTOFF_HOURS}h._\n")
        return "\n".join(out)

    out.append(
        "Likely causes: market closed/resolved, scraper dropped them from the "
        "filter, or scraper outage. Worth investigating per row.\n"
    )
    out.append("| Platform | Ticker | Title | Last updated | Age |")
    out.append("|---|---|---|---|---|")
    for r in rows:
        # SQLite strips timezone info on read even though the column is
        # declared DateTime(timezone=True); Postgres preserves it.
        # Normalize to UTC-aware so the subtraction works on both backends.
        latest = r.latest if r.latest.tzinfo else r.latest.replace(tzinfo=UTC)
        age_h = int((now - latest).total_seconds() / 3600)
        out.append(
            f"| {r.platform} | `{r.ticker}` | {_truncate(r.title, 50)} | "
            f"{_fmt_ts(r.latest)} | {age_h}h |"
        )
    return "\n".join(out) + "\n"


# ---- Formatting helpers --------------------------------------------------


def _format_header() -> str:
    now = datetime.now(UTC)
    return (
        f"# Prediction-market DB audit — {now.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        "Generated by `scripts/audit_prediction_markets.py`. Read-only; "
        "safe to re-run as often as useful.\n"
    )


def _fmt_ts(ts: datetime | None) -> str:
    if ts is None:
        return "—"
    return ts.strftime("%Y-%m-%d %H:%M")


def _fmt_price(price: float | None) -> str:
    if price is None:
        return "—"
    return f"{price:.0f}"


def _fmt_volume(v: float | None) -> str:
    if v is None or v <= 0:
        return "—"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:.0f}"


def _truncate(s: str | None, n: int) -> str:
    if not s:
        return ""
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _drain(it: Iterable[Any]) -> None:
    """Touch every row in an iterable so SQLAlchemy realizes the cursor.
    Reserved for any future query whose side effect matters; currently
    unused but kept as a tiny utility."""
    for _ in it:
        pass


if __name__ == "__main__":
    run()
