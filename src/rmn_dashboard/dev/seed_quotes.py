"""Seed plausible TickerQuote rows so Panel 2 renders off-season.

Same philosophy as ``seed_irma`` and ``seed_ian``: give a developer a
populated dashboard without pointing at the live yfinance feed. The
prices are not meant to be accurate — they're stable, plausible values
keyed off each ticker's symbol so a developer who refreshes the page
sees the same numbers each time.

Why deterministic, not random:

  * A seed that picks fresh random prices on every run produces
    different "last price" / "change %" values across reloads, which
    looks like a live feed but isn't — visually confusing during dev.
  * Determinism also makes the test suite happy: assert against
    ``UVE = 21.45`` and the seed will always satisfy that.

Usage::

    python -m rmn_dashboard.dev.seed_quotes
    python -m rmn_dashboard.dev.seed_quotes --clear   # drop & re-seed

The seed refuses to run against a non-SQLite ``DATABASE_URL`` for the
same reason ``seed_irma`` does — synthetic data should never land in
prod (Render's Postgres) or in a developer's accidentally-pointed-at-
prod laptop.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.orm import Session

from rmn_dashboard.config import settings
from rmn_dashboard.data.universe import Universe, UniverseEntry, load_universe
from rmn_dashboard.database import SessionLocal, normalize_database_url
from rmn_dashboard.models import TickerQuote

logger = logging.getLogger(__name__)


# Stable scrape timestamp — rendered as "as of yesterday at close" in
# the UI when the dev DB sits idle. Picked deterministically so the
# whole batch shares one as_of (matches the production batch pattern).
SEED_AS_OF = datetime(2026, 4, 24, 21, 0, tzinfo=UTC)


def _require_sqlite() -> None:
    """Refuse to run against a non-SQLite DB.

    Same contract as ``seed_irma._require_sqlite`` — the seed is for
    local dev only; synthetic data should never land in prod.
    """
    url = normalize_database_url(settings.database_url)
    if not url.startswith("sqlite"):
        raise SystemExit(
            "seed_quotes refuses to run against a non-SQLite database "
            f"(DATABASE_URL={settings.database_url!r}). "
            "This seed is for local dev only."
        )


# ----- Deterministic price generation -------------------------------------


def _ticker_seed(ticker: str) -> int:
    """Return a deterministic 32-bit-ish int from a ticker symbol.

    SHA1 keeps it stable across Python versions (unlike ``hash()``,
    which is randomized per process) and avoids any fancy crypto we
    don't need. Only use the first 4 bytes — int is fine.
    """
    digest = hashlib.sha1(ticker.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big", signed=False)


def _mock_quote_for(entry: UniverseEntry) -> dict[str, float | int | None]:
    """Compose a plausible-but-fake quote for one universe entry.

    Price ranges are sector-tuned (insurers in tens, utilities in
    50–100, big-name commercial $200+) so the seeded ticker grid
    "feels" like reality even though the numbers are made up.
    """
    seed = _ticker_seed(entry.ticker)
    sector_base = {
        "insurer": 30.0,
        "reinsurer": 130.0,
        "homebuilder": 90.0,
        "utility": 65.0,
        # Cat bond ETFs trade in the $20-25 range historically — UCITS
        # cat bond fund NAVs cluster there. Use a tighter dev-seed
        # range so the panel renders a plausible number off-season.
        "cat_bond_etf": 22.0,
        # KBWP (P&C insurance index ETF) has historically traded in the
        # $80-100 band; pick a base that lets the multiplier spread
        # straddle that range. Day 20 added pc_index alongside cat bond
        # ETFs in Panel 3 ("Hurricane risk capital").
        "pc_index": 90.0,
    }[entry.sector]

    # Spread the price 0.5x-1.8x the sector base, deterministic per ticker.
    multiplier = 0.5 + ((seed % 1000) / 1000.0) * 1.3
    last_price = round(sector_base * multiplier, 2)

    # Change % in [-3.5, +3.5], deterministic per ticker. Bias slightly
    # so seeded data isn't always all-green / all-red — looks healthier.
    change_pct_raw = ((seed >> 10) % 700) / 100.0 - 3.5
    change_pct = round(change_pct_raw, 2)
    prior_close = round(last_price / (1.0 + change_pct / 100.0), 2)
    change_amount = round(last_price - prior_close, 2)

    # Volume + market cap: bracket by sector_base so order-of-magnitude
    # is right. Volume 100k-5M, market cap $500M-$30B.
    volume = int(100_000 + (seed % 4_900_000))
    market_cap = float(500_000_000 + ((seed >> 5) % 30_000_000_000))

    return {
        "last_price": last_price,
        "prior_close": prior_close,
        "change_amount": change_amount,
        "change_percent": change_pct,
        "volume": volume,
        "market_cap": market_cap,
    }


# ----- Persistence --------------------------------------------------------


def _clear_existing(db: Session) -> None:
    """Drop every TickerQuote row stamped at SEED_AS_OF.

    Scoped to the seed timestamp so a developer who has been running
    real ingest scrapes against the dev DB doesn't lose their history.
    Expunges the identity map afterward — same SQLite-PK-reuse
    workaround we use in ``seed_irma._clear_existing``.
    """
    db.execute(delete(TickerQuote).where(TickerQuote.as_of == SEED_AS_OF))
    db.commit()
    db.expunge_all()


def _insert_rows(db: Session, universe: Universe) -> int:
    rows: list[TickerQuote] = []
    for entry in universe.tickers:
        # Skip tickers that already have a row at SEED_AS_OF — re-running
        # without --clear is idempotent.
        existing = db.query(TickerQuote).filter_by(ticker=entry.ticker, as_of=SEED_AS_OF).first()
        if existing is not None:
            continue
        info = _mock_quote_for(entry)
        rows.append(
            TickerQuote(
                ticker=entry.ticker,
                last_price=info["last_price"],
                prior_close=info["prior_close"],
                change_amount=info["change_amount"],
                change_percent=info["change_percent"],
                volume=info["volume"],
                market_cap=info["market_cap"],
                currency="USD",
                source="dev-seed",
                as_of=SEED_AS_OF,
            )
        )
    if rows:
        db.add_all(rows)
    return len(rows)


def seed(db: Session, *, clear: bool = False, universe: Universe | None = None) -> dict:
    """Seed deterministic mock quotes for every universe ticker.

    Returns a small summary the CLI prints. Idempotent — repeat runs
    don't insert duplicates. ``clear=True`` drops everything stamped
    SEED_AS_OF before re-inserting.
    """
    if universe is None:
        universe = load_universe()
    if clear:
        _clear_existing(db)
    inserted = _insert_rows(db, universe)
    return {
        "tickers_in_universe": len(universe.tickers),
        "rows_inserted": inserted,
        "as_of": SEED_AS_OF.isoformat(),
    }


def _cli(argv: list[str] | None = None) -> int:
    """Stand-alone entry point — builds its own session + logging."""
    parser = argparse.ArgumentParser(
        description="Seed deterministic TickerQuote rows for dev Panel 2 rendering."
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Drop every TickerQuote stamped at SEED_AS_OF before re-seeding.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    _require_sqlite()

    db = SessionLocal()
    try:
        summary = seed(db, clear=args.clear)
        db.commit()
        print(
            f"Seeded {summary['rows_inserted']} of "
            f"{summary['tickers_in_universe']} universe tickers "
            f"(as_of={summary['as_of']})."
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(_cli())
