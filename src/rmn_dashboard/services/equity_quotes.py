"""Equity-quote read helpers — Panel 2's view of the hurricane universe.

``ingest_yfinance`` writes one ``TickerQuote`` snapshot per ticker per
scrape run. Panel 2 needs the *latest* snapshot per ticker, joined back
to the curated universe so each row carries its sector, key states, and
editorial relevance — exactly what ``markets.py`` does for prediction
markets, parameterized for our equity model.

Filter helpers:

  * ``sectors`` — narrow to "insurer", "utility", etc. for the Panel 2
    filter pills.
  * ``states`` — narrow to tickers whose ``key_states`` intersect a set
    of affected states (the cone-overlap highlight).

Reinsurers (``key_states == ()``) are *never* returned by the state
filter — global books, no per-state precision. Same editorial rule the
universe loader's ``tickers_for_states`` enforces.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rmn_dashboard.data.universe import (
    Sector,
    Universe,
    load_universe,
    tickers_for_states,
)
from rmn_dashboard.models import TickerQuote


def _latest_quote_subquery():
    """Group-by subquery: most recent ``as_of`` per ticker."""
    return (
        select(
            TickerQuote.ticker,
            func.max(TickerQuote.as_of).label("max_ts"),
        )
        .group_by(TickerQuote.ticker)
        .subquery()
    )


def _quote_to_dict(quote: TickerQuote) -> dict[str, Any]:
    """Render a TickerQuote ORM row as a JSON-serializable dict.

    Keeps the date/datetime formatting in one place so the Panel 2 JS
    client (and any downstream RMN newsletter consumer) sees a stable
    ISO-8601 string instead of having to parse a SQLAlchemy object.
    """
    return {
        "last_price": quote.last_price,
        "prior_close": quote.prior_close,
        "change_amount": quote.change_amount,
        "change_percent": quote.change_percent,
        "currency": quote.currency,
        "volume": quote.volume,
        "market_cap": quote.market_cap,
        "source": quote.source,
        "as_of": _isoformat(quote.as_of),
    }


def _isoformat(value: datetime | None) -> str | None:
    """Stable ISO-8601 string for JSON output. ``None`` passes through.

    SQLite drops the timezone tag from ``DateTime(timezone=True)``
    columns on read — every value comes back naive. Tag naive datetimes
    as UTC before serializing so the JSON API never ships an ambiguous
    timestamp (and matches Postgres's behavior in prod). All our writes
    use ``datetime.now(UTC)`` so this is a faithful re-tag, not a guess.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def latest_universe_quotes(
    db: Session,
    *,
    sectors: Iterable[Sector] | None = None,
    states: Iterable[str] | None = None,
    universe: Universe | None = None,
) -> list[dict[str, Any]]:
    """Return one row per universe ticker, joined to its latest quote.

    Output shape (one entry per ticker)::

        {
          "ticker": "UVE",
          "name": "Universal Insurance Holdings",
          "sector": "insurer",
          "hurricane_relevance": "high",
          "key_states": ["FL"],
          "notes": "...",
          "quote": {
              "last_price": 21.45,
              "change_amount": 1.45,
              "change_percent": 7.25,
              "as_of": "2026-04-24T17:00:00+00:00",
              ...
          } | null    # null if no scrape has produced a row yet
        }

    Tickers with no quote yet still appear in the response — the UI
    renders them with a "—" placeholder rather than dropping them
    entirely. This keeps the full curated universe visible even during
    a scraper incident.

    ``sectors`` and ``states`` are independent filters; passing both
    intersects them. ``states=[]`` is treated as "no state filter," not
    "filter to nothing" — same convention as ``tickers_for_states``.
    """
    if universe is None:
        universe = load_universe()

    # Subset the universe before hitting the DB so a 35-ticker scrape
    # doesn't have to dedupe rows we'd discard.
    entries = universe.tickers
    if sectors is not None:
        wanted_sectors = set(sectors)
        entries = tuple(e for e in entries if e.sector in wanted_sectors)
    if states is not None:
        # ``tickers_for_states`` returns a subset; intersect by ticker.
        state_filtered = {e.ticker for e in tickers_for_states(universe, states)}
        if state_filtered:
            entries = tuple(e for e in entries if e.ticker in state_filtered)
        else:
            # Empty state list → no filter (matches universe loader semantics).
            pass

    target_tickers = [e.ticker for e in entries]
    if not target_tickers:
        return []

    # Pull the latest TickerQuote per ticker for the targeted set.
    latest_per_ticker = _latest_quote_subquery()
    stmt = (
        select(TickerQuote)
        .join(
            latest_per_ticker,
            (TickerQuote.ticker == latest_per_ticker.c.ticker)
            & (TickerQuote.as_of == latest_per_ticker.c.max_ts),
        )
        .where(TickerQuote.ticker.in_(target_tickers))
    )
    quotes_by_ticker = {q.ticker: q for q in db.scalars(stmt).all()}

    # Compose the response in universe order so the UI sees a stable
    # roster across renders (keeps filter-pill toggles from re-shuffling
    # the grid every poll).
    payload: list[dict[str, Any]] = []
    for entry in entries:
        quote = quotes_by_ticker.get(entry.ticker)
        payload.append(
            {
                "ticker": entry.ticker,
                "name": entry.name,
                "sector": entry.sector,
                "hurricane_relevance": entry.hurricane_relevance,
                "key_states": list(entry.key_states),
                "notes": entry.notes,
                "quote": _quote_to_dict(quote) if quote is not None else None,
            }
        )
    return payload
