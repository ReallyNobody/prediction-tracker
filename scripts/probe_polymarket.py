#!/usr/bin/env python3
"""Probe Polymarket's Gamma API for hurricane-related markets.

Day 36 — Polymarket equivalent of scripts/probe_kalshi.py. Goal is to
discover the actual response shape of Polymarket's Gamma API before
writing the full ingest in Day 37. Without this, the scraper gets
written against documented-but-possibly-stale API expectations and
risks shipping subtle field-name or type-coercion bugs.

What we want to know after a probe run:

  * Which fields does each market object actually expose? (id, slug,
    question, outcomes, outcomePrices, volume, liquidity, endDate,
    closed, active, tags, ...)
  * How are prices encoded? Plausible expectations: a JSON-encoded
    string list like '["0.42", "0.58"]', or floats, or something else.
  * How does volume vs liquidity differ? Polymarket reports both;
    we need to choose one for the Panel 4 readout.
  * Is there a tag-based filter (e.g. ?tag=hurricane) that gives us
    the curated subset Polymarket itself uses for
    polymarket.com/predictions/hurricane, or do we have to fetch all
    markets and post-filter by title?
  * Do markets group under a parent "event" (like Kalshi), and if so,
    do we want to surface event-level metadata?

Strategy:

  1. GET /markets with closed=false&archived=false, paginated.
  2. Print response keys + sample shape verbatim.
  3. Filter by hurricane keyword regex (same pattern as
     scripts/probe_kalshi.py — deliberately omits a bare "storm").
  4. Print matched markets with all their fields, so the operator can
     eyeball field names and decide which to persist in Day 37.

Usage
-----
    cd ~/Dev/Predict
    . .venv/bin/activate
    python scripts/probe_polymarket.py

Read-only. No auth required (Polymarket Gamma API is public — no API
key, no signed requests, none of the Kalshi-style auth dance). No DB
writes. Safe to run repeatedly.

If the API URL or response shape has changed since this script was
written, the script will print whatever it gets and let the operator
adapt — diagnostic, not a happy-path runner.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from collections import Counter
from typing import Any

import httpx

# Polymarket Gamma API — public, no auth. Documented at
# https://docs.polymarket.com/. The /markets endpoint is the live
# market list; /events groups markets by parent event.
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"

# Hurricane-adjacent keywords. Same regex pattern as
# scripts/probe_kalshi.py — deliberately omits a bare "storm" since it
# pulls in sports teams and snowstorms. Tropical / cyclone / hurricane /
# landfall / Atlantic basin all caught explicitly.
KEYWORDS = re.compile(
    r"\b(hurricane|tropical|cyclone|landfall|atlantic\s+(?:basin|season|hurricane))\b",
    re.IGNORECASE,
)

# Polymarket's /markets accepts a `limit` (capped around 500 per
# request based on community reports) and an `offset` for pagination.
# Gentle pacing because we're a courtesy guest on a public API.
PER_PAGE = 200
PER_PAGE_SLEEP = 0.4
MAX_PAGES = 25  # 25 * 200 = 5,000 markets ceiling; should be enough.
HTTP_TIMEOUT = 30.0


def _client() -> httpx.Client:
    """HTTPx client with a courteous User-Agent and reasonable timeout."""
    return httpx.Client(
        base_url=GAMMA_BASE_URL,
        timeout=HTTP_TIMEOUT,
        headers={
            # Identify ourselves so Polymarket can rate-limit / contact
            # us if our usage misbehaves. Same convention as the SEC
            # User-Agent we use for NHC.
            "User-Agent": ("Risk Market News probe (research@riskmarketnews.com)"),
            "Accept": "application/json",
        },
    )


def _get_markets_page(client: httpx.Client, offset: int) -> list[dict[str, Any]]:
    """GET /markets with closed=false&archived=false. Returns raw list."""
    params: dict[str, Any] = {
        "limit": PER_PAGE,
        "offset": offset,
        "closed": "false",
        "archived": "false",
    }
    response = client.get("/markets", params=params)
    response.raise_for_status()
    payload = response.json()
    # Polymarket's /markets sometimes returns a bare list, sometimes
    # an object with a `markets` key — handle both defensively.
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and "markets" in payload:
        markets = payload["markets"]
        if isinstance(markets, list):
            return markets
    print(f"WARNING: unexpected /markets payload shape: {type(payload)}", file=sys.stderr)
    return []


def _paginate_markets(client: httpx.Client) -> list[dict[str, Any]]:
    """Pull active markets across pages. Returns whatever we got — if a
    page fails, we keep partial results so the diagnostic still runs."""
    markets: list[dict[str, Any]] = []
    for page in range(MAX_PAGES):
        offset = page * PER_PAGE
        try:
            batch = _get_markets_page(client, offset)
        except httpx.HTTPError as exc:
            print(f"WARNING: stopping at page {page}: {exc}", file=sys.stderr)
            break
        if not batch:
            break
        markets.extend(batch)
        print(f"  page {page + 1}: +{len(batch)} markets (running total: {len(markets)})")
        if len(batch) < PER_PAGE:
            break
        time.sleep(PER_PAGE_SLEEP)
    else:
        print(f"  stopped at MAX_PAGES={MAX_PAGES}; there may be more beyond this.")
    return markets


def _print_field_summary(markets: list[dict[str, Any]]) -> None:
    """Tally field names across all market objects so the operator can
    see which fields are universal vs sometimes-missing."""
    if not markets:
        return
    field_counts: Counter[str] = Counter()
    for market in markets:
        for key in market:
            field_counts[key] += 1
    total = len(markets)
    print(f"\nField presence across {total} markets (sorted by frequency):")
    for field, count in field_counts.most_common():
        pct = (count / total) * 100
        print(f"  {field:<32s}  {count:>5d} / {total}  ({pct:5.1f}%)")


def _hurricane_hits(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter markets whose title (`question`) matches the hurricane regex."""
    hits: list[dict[str, Any]] = []
    for market in markets:
        title = market.get("question") or market.get("title") or ""
        if KEYWORDS.search(title):
            hits.append(market)
    return hits


def _print_hits(hits: list[dict[str, Any]]) -> None:
    if not hits:
        print("\nNo hurricane-related markets matched the keyword regex.")
        print("Hurricane markets typically appear during/near Atlantic season")
        print("(June - November); off-season the basin may be empty.")
        return

    print(f"\nHurricane-candidate markets: {len(hits)}\n")

    # Print the first hit as a full JSON dump so the operator can see
    # every field name and value. Subsequent hits get a one-line summary.
    first = hits[0]
    print("--- Full JSON dump of the first hit (use to design the scraper) ---")
    print(json.dumps(first, indent=2, default=str))
    print("--- end first-hit dump ---\n")

    print("Other hits (one-line summary, see fields above for shape):")
    for market in hits[1:21]:  # cap so terminal output stays readable
        title = (market.get("question") or market.get("title") or "")[:80]
        slug = market.get("slug") or "?"
        volume = market.get("volume") or "?"
        end_date = market.get("endDate") or market.get("end_date") or "?"
        print(f"  - {slug}")
        print(f"    title:  {title}")
        print(f"    volume: {volume}    end_date: {end_date}")
    if len(hits) > 21:
        print(f"  ... and {len(hits) - 21} more")


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    print(f"Probing Polymarket Gamma API at {GAMMA_BASE_URL}")
    print("Pulling open markets (closed=false, archived=false)...")

    with _client() as client:
        markets = _paginate_markets(client)

    print(f"\nTotal active markets collected: {len(markets)}")
    if not markets:
        print("No markets returned. Check API URL and connectivity.")
        return 1

    _print_field_summary(markets)

    hits = _hurricane_hits(markets)
    _print_hits(hits)

    print("\nDone. Paste the field-presence table and the first-hit JSON dump")
    print("back into the project chat — Day 37 builds the real scraper from it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
