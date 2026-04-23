#!/usr/bin/env python3
"""Probe Kalshi's public event namespace for hurricane-related markets.

Week 2 Day 6 — ticker discovery. The legacy scraper hardcoded weather-station
series tickers ("KXHIGHLAX", etc.) that aren't hurricane markets. Before we
can plug real inputs into ``fetch_hurricane_markets``, we need to know what
Kalshi actually calls its hurricane/tropical-storm markets today.

Strategy: walk /events with ``status=open`` (cursor-paginated), filter titles
by a keyword regex, and group hits by ``series_ticker``. The output is a
shortlist we can paste into a config or turn into a seeded table.

Usage
-----
    cd ~/Dev/Predict
    . .venv/bin/activate
    python scripts/probe_kalshi.py

Requires ``KALSHI_API_KEY_ID`` and ``KALSHI_PRIVATE_KEY_PATH`` in ``.env``.
Read-only. No DB writes. Safe to run repeatedly.
"""

from __future__ import annotations

import re
import sys
import time
from collections import defaultdict
from typing import Any

import httpx

from rmn_dashboard.scrapers.kalshi import (
    HURRICANE_SERIES,
    KalshiConfigError,
    client_from_settings,
    fetch_hurricane_markets,
)

# Hurricane-adjacent keywords. Deliberately omits a bare "storm" — that
# word alone pulls in sports teams (Melbourne Storm, Orlando Storm) and
# snowstorm / thunderstorm markets we don't want. "Tropical storm" is
# matched via the "tropical" token; landfall / cyclone / hurricane /
# Atlantic-basin markets are covered explicitly.
KEYWORDS = re.compile(
    r"\b(hurricane|tropical|cyclone|landfall|atlantic\s+(?:basin|season|hurricane))\b",
    re.IGNORECASE,
)

EVENTS_PAGE_LIMIT = 200  # Kalshi's /events accepts up to 200 per page.
PER_PAGE_SLEEP = 0.5  # Kalshi rate-limits aggressively; ~2 req/sec is safe.
MAX_PAGES = 60  # 60 * 200 = 12,000 event ceiling. Enough to cover the surface.
MAX_429_RETRIES = 4  # Exponential backoff: 1s, 2s, 4s, 8s.


def _get_events_page(client: Any, params: dict[str, Any]) -> dict[str, Any]:
    """GET /events with exponential backoff on 429."""
    for attempt in range(MAX_429_RETRIES + 1):
        try:
            return client.get("/events", params=params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 and attempt < MAX_429_RETRIES:
                wait = 2**attempt
                print(f"    rate limited; backing off {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("unreachable")  # pragma: no cover


def _paginate_events(client: Any) -> list[dict[str, Any]]:
    """Pull open events across cursor pages. Returns whatever we got — if the
    caller's loop is interrupted by a hard failure, we still want the partial
    list to flow through to the report."""
    events: list[dict[str, Any]] = []
    cursor: str | None = None

    for page in range(1, MAX_PAGES + 1):
        params: dict[str, Any] = {"status": "open", "limit": EVENTS_PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor

        try:
            payload = _get_events_page(client, params)
        except httpx.HTTPError as exc:
            # Keep what we have so the report can still run over partial data.
            print(f"WARNING: stopping at page {page}: {exc}", file=sys.stderr)
            break

        batch = payload.get("events", [])
        events.extend(batch)
        print(f"  page {page}: +{len(batch)} events (running total: {len(events)})")

        cursor = payload.get("cursor") or None
        if not cursor:
            break

        time.sleep(PER_PAGE_SLEEP)
    else:
        print(f"  stopped at MAX_PAGES={MAX_PAGES}; there may be more beyond this.")

    return events


def _group_hits(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Keep only events whose title matches our keyword regex, grouped by series."""
    by_series: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        title = event.get("title") or ""
        if KEYWORDS.search(title):
            by_series[event.get("series_ticker") or "<no-series>"].append(event)
    return by_series


def _print_report(by_series: dict[str, list[dict[str, Any]]]) -> None:
    if not by_series:
        print("\nNo hurricane-related events found in /events?status=open.")
        print("Hurricane markets may only list during/near Atlantic season (Jun–Nov).")
        return

    total = sum(len(v) for v in by_series.values())
    print(f"\nHurricane-candidate events: {total} across {len(by_series)} series\n")

    for series in sorted(by_series):
        hits = by_series[series]
        print(f"  {series}  ({len(hits)} events)")
        for event in hits[:5]:
            ticker = event.get("event_ticker") or "?"
            title = event.get("title") or ""
            print(f"    - {ticker}: {title}")
        if len(hits) > 5:
            print(f"    ... and {len(hits) - 5} more")
        print()


def main() -> int:
    try:
        client = client_from_settings()
    except KalshiConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        print("Pulling open events from Kalshi (cursor-paginated)...")
        events = _paginate_events(client)

        print(f"\nTotal open events collected: {len(events)}")
        _print_report(_group_hits(events))

        # End-to-end validation: fetch + normalize the configured hurricane
        # series through the real scraper. Proves the full stack works against
        # live Kalshi data, not just mocked transport.
        print("\n" + "=" * 60)
        print(f"Live fetch via fetch_hurricane_markets({list(HURRICANE_SERIES)})")
        print("=" * 60)
        markets = fetch_hurricane_markets(HURRICANE_SERIES, client=client)
        print(f"Normalized {len(markets)} markets:\n")
        for market in markets:
            print(f"  [{market.series_ticker}] {market.ticker}")
            print(f"    title:        {market.title}")
            print(f"    yes/no bid:   ${market.yes_bid:.2f} / ${market.no_bid:.2f}")
            print(f"    yes/no ask:   ${market.yes_ask:.2f} / ${market.no_ask:.2f}")
            print(f"    vol (24h):    ${market.volume_24h:,.0f}")
            print(f"    open interest: {market.open_interest:,.0f}")
            print(f"    url:          {market.url}")
            print()
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
