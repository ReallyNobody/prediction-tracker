"""yfinance scraper — pulls a quote snapshot per ticker in the universe.

Why yfinance, not a paid feed:

  * Free, no API key, ~15-minute delayed quotes — adequate for a
    journalism-style ticker that doesn't claim live precision.
  * Most indie financial dashboards are built on it; the upgrade
    path to Finnhub / Polygon if we outgrow it is straightforward.

Why ``fast_info``, not ``info``:

  * ``Ticker(t).info`` pulls a fat dict (~70 fields) with several
    expensive subqueries; can take 1-2s per ticker, occasionally
    times out, and breaks when Yahoo shifts its internal endpoints.
  * ``Ticker(t).fast_info`` returns a lazy accessor with the handful
    of fields we actually need (``last_price``, ``previous_close``,
    ``last_volume``, ``market_cap``, ``currency``). Faster, more
    stable across yfinance upgrades.

Testing pattern:

  * The fetch entrypoint takes an injectable ``fetch_one`` callable.
    Tests pass a stub that returns canned dicts; production wires in
    ``_default_fetch_one`` which is a thin wrapper over yfinance.
  * No pandas in the snapshot dataclass — kept as float / int / str
    so it survives pickling, dict-conversion, and the Pydantic /
    dataclass boundaries cleanly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from rmn_dashboard.data.universe import Universe, load_universe

logger = logging.getLogger(__name__)


# Fetcher takes a ticker symbol and returns either a dict of quote
# fields or None if the lookup failed (network glitch, delisted symbol,
# Yahoo dropping the ticker temporarily). The scraper logs-and-skips
# None responses so a single bad ticker doesn't tank the whole batch.
QuoteFetcher = Callable[[str], dict[str, Any] | None]


@dataclass(frozen=True)
class QuoteSnapshot:
    """One quote sample for one ticker, normalized off yfinance's
    ``fast_info`` accessor.

    Frozen so a snapshot can't be mutated after construction — the
    scraper hands instances to the persistence layer, which writes
    rows once and forgets them.
    """

    ticker: str
    last_price: float
    prior_close: float | None
    change_amount: float | None
    change_percent: float | None
    volume: int | None
    market_cap: float | None
    currency: str
    fetched_at: datetime
    source: str = "yfinance"


def _default_fetch_one(ticker: str) -> dict[str, Any] | None:
    """Production fetch: pull this ticker's fast_info from yfinance.

    Catches *any* exception from yfinance (it raises a wide variety:
    HTTPError, KeyError on missing fields, ValueError when Yahoo
    returns malformed JSON) and returns None — the per-ticker
    log-and-skip pattern keeps a single bad symbol from wiping the
    whole scrape.

    Imported lazily so test environments that haven't installed
    yfinance can still import this module (and stub the fetcher).
    """
    try:
        import yfinance  # noqa: PLC0415  — lazy import keeps tests light
    except ImportError:
        logger.exception("yfinance not installed; cannot fetch %s", ticker)
        return None

    try:
        fi = yfinance.Ticker(ticker).fast_info
        last_price = _coerce_float(fi.get("lastPrice") or getattr(fi, "last_price", None))
        if last_price is None:
            logger.info("yfinance returned no last_price for %s; skipping", ticker)
            return None
        return {
            "last_price": last_price,
            "previous_close": _coerce_float(
                fi.get("previousClose") or getattr(fi, "previous_close", None)
            ),
            "last_volume": _coerce_int(fi.get("lastVolume") or getattr(fi, "last_volume", None)),
            "market_cap": _coerce_float(fi.get("marketCap") or getattr(fi, "market_cap", None)),
            "currency": str(fi.get("currency") or getattr(fi, "currency", None) or "USD").upper(),
        }
    except Exception:  # noqa: BLE001 — yfinance raises a wide variety; log-and-skip
        logger.exception("yfinance fetch failed for %s", ticker)
        return None


def _coerce_float(value: Any) -> float | None:
    """Best-effort float coercion. None / NaN / empty / non-numeric → None."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # NaN check without importing math: NaN != NaN
    if f != f:
        return None
    return f


def _coerce_int(value: Any) -> int | None:
    """Coerce to int; None / NaN / non-numeric → None."""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_snapshot(ticker: str, info: dict[str, Any], *, fetched_at: datetime) -> QuoteSnapshot:
    """Compose a frozen ``QuoteSnapshot`` from a fetched info dict.

    Computes the pre-rendered ``change_amount`` / ``change_percent``
    on the way through so the persistence + service layers never
    have to.
    """
    last_price = float(info["last_price"])
    prior_close = info.get("previous_close")
    change_amount: float | None = None
    change_percent: float | None = None
    if prior_close not in (None, 0):
        change_amount = round(last_price - prior_close, 4)
        change_percent = round((change_amount / prior_close) * 100.0, 4)

    return QuoteSnapshot(
        ticker=ticker,
        last_price=last_price,
        prior_close=prior_close,
        change_amount=change_amount,
        change_percent=change_percent,
        volume=info.get("last_volume"),
        market_cap=info.get("market_cap"),
        currency=str(info.get("currency", "USD")),
        fetched_at=fetched_at,
    )


def fetch_universe_quotes(
    universe: Universe | None = None,
    *,
    fetch_one: QuoteFetcher | None = None,
    fetched_at: datetime | None = None,
) -> list[QuoteSnapshot]:
    """Fetch a quote per ticker in ``universe``; skip-and-log per-ticker failures.

    Defaults: load the bundled universe, use the production yfinance
    fetcher, stamp every snapshot with the same UTC ``fetched_at`` so
    a batch reads as one logical scrape.

    Tests pass a fake ``fetch_one`` (returning canned dicts) and
    optionally a fixed ``fetched_at`` for deterministic comparisons.
    """
    if universe is None:
        universe = load_universe()
    if fetch_one is None:
        fetch_one = _default_fetch_one
    if fetched_at is None:
        fetched_at = datetime.now(UTC)

    snapshots: list[QuoteSnapshot] = []
    for entry in universe.tickers:
        info = fetch_one(entry.ticker)
        if info is None:
            continue
        if "last_price" not in info or info["last_price"] is None:
            logger.info("No last_price for %s; skipping", entry.ticker)
            continue
        snapshots.append(_to_snapshot(entry.ticker, info, fetched_at=fetched_at))

    logger.info("yfinance scrape returned %d/%d snapshots", len(snapshots), len(universe.tickers))
    return snapshots
