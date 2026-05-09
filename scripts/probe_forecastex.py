#!/usr/bin/env python3
"""Probe ForecastEx public data feeds for hurricane-related contracts.

Day 39 — ForecastEx equivalent of scripts/probe_polymarket.py and
scripts/probe_kalshi.py. Goal: discover the actual CSV column shape
ForecastEx publishes before writing the full ingest in Day 40.

ForecastEx is the CFTC-regulated DCM owned by Interactive Brokers
(approved June 2024). They publish public CSV feeds at
forecastex.com/data:

  * Prices CSV — daily end-of-day closing prices.
  * Pairs CSV  — refreshed every ~10 minutes.

Both are unauthenticated. We don't need an IBKR account to ingest.

What we want to know after a probe run:

  * Exact column names in each CSV (the ForecastEx site doesn't
    publish a stable schema doc that I could find).
  * How are contracts identified? (Symbol? Numeric ID? Both?)
  * Is there a category/tag column we can use to find hurricane
    contracts, or do we have to filter on the contract title?
  * What volume / open-interest / liquidity column exists, if any —
    determines whether ForecastEx data slots into our existing
    PredictionMarket model or if we need new fields.
  * Sample rows for hurricane contracts so we can sanity-check the
    headline format we'd produce ("$X traded on ForecastEx — ...").

Strategy:

  1. Download Prices CSV and Pairs CSV. Print HTTP status + size.
  2. Print column headers + first 3 data rows verbatim.
  3. Filter by hurricane keyword regex (same pattern as the other two
     probe scripts, deliberately omitting bare "storm").
  4. Print matched contracts with all columns so the operator can
     decide field mapping for Day 40.

Usage
-----
    cd ~/Dev/Predict
    . .venv/bin/activate
    python scripts/probe_forecastex.py

If the default URLs 404, find the real CSV paths on the
forecastex.com/data portal page in a browser and re-run with
overrides:

    python scripts/probe_forecastex.py \
        --prices-url https://data.forecastex.com/prices.csv \
        --pairs-url  https://data.forecastex.com/pairs.csv

Read-only. No auth. No DB writes. Safe to run repeatedly.
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import re
import sys

import httpx

# Best-guess URLs based on the forecastex.com/data portal references in
# IBKR's documentation. The ForecastEx site publishes the canonical
# paths but they aren't in any docs we could grep — confirm against the
# live portal in a browser before relying on these defaults.
DEFAULT_PRICES_URL = "https://forecastex.com/data/prices.csv"
DEFAULT_PAIRS_URL = "https://forecastex.com/data/pairs.csv"

# Same hurricane-adjacent regex as probe_kalshi.py / probe_polymarket.py.
# Deliberately omits a bare "storm" — pulls in sports teams + snowstorms.
KEYWORDS = re.compile(
    r"\b(hurricane|tropical|cyclone|landfall|atlantic\s+(?:basin|season|hurricane))\b",
    re.IGNORECASE,
)

HTTP_TIMEOUT = 30.0

# Columns we'll try in order to find the contract title for keyword
# filtering. ForecastEx hasn't published a public schema reference, so
# this is a best-guess list; if none match we print the columns and ask
# the operator to re-run with --title-col.
_TITLE_COL_CANDIDATES = [
    "Description",
    "description",
    "Title",
    "title",
    "ContractName",
    "contract_name",
    "contractName",
    "Name",
    "name",
    "Question",
    "question",
    "MarketName",
    "market_name",
    "marketName",
]


def _client() -> httpx.Client:
    """HTTPx client with a courteous User-Agent and follow_redirects on
    in case the data portal serves CSVs through a CDN redirect."""
    return httpx.Client(
        timeout=HTTP_TIMEOUT,
        headers={
            "User-Agent": "Risk Market News probe (research@riskmarketnews.com)",
            "Accept": "text/csv, */*",
        },
        follow_redirects=True,
    )


def _fetch(client: httpx.Client, url: str) -> tuple[int, str, str]:
    """Fetch a URL. Returns (status_code, content_type, body_text).
    Doesn't raise — diagnostic mode, we want to see what came back even
    when the server refused or redirected unexpectedly."""
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        print(f"  ERROR: {url} → {exc}", file=sys.stderr)
        return (0, "", "")
    return (
        response.status_code,
        response.headers.get("content-type", ""),
        response.text,
    )


def _parse_csv(body: str) -> tuple[list[str], list[dict[str, str]]]:
    """Parse CSV text into (headers, rows). Tolerant of a leading BOM
    and blank lines. Returns ([], []) if the body isn't parseable."""
    if not body.strip():
        return ([], [])
    if body.startswith("﻿"):
        body = body[1:]
    reader = csv.DictReader(io.StringIO(body))
    headers = list(reader.fieldnames or [])
    rows = list(reader)
    return (headers, rows)


def _pick_title_column(headers: list[str], override: str | None) -> str | None:
    if override:
        return override if override in headers else None
    for candidate in _TITLE_COL_CANDIDATES:
        if candidate in headers:
            return candidate
    return None


def _print_feed(
    label: str,
    status: int,
    ctype: str,
    body: str,
    headers: list[str],
    rows: list[dict[str, str]],
    title_col_override: str | None,
) -> None:
    """Print a one-feed diagnostic block."""
    print(f"\n=== {label} ===")
    print(f"  HTTP status:  {status}")
    print(f"  Content-Type: {ctype}")
    print(f"  Body length:  {len(body):,} bytes")

    if not headers:
        if status and status != 200:
            print("  (non-200 — body preview below)")
        elif body.strip():
            print("  (body returned but did not parse as CSV — preview below)")
        else:
            print("  (empty body)")
        if body:
            print("\n  --- first 500 bytes ---")
            print("  " + body[:500].replace("\n", "\n  "))
            print("  --- end preview ---")
        return

    print(f"  Columns ({len(headers)}):")
    for col in headers:
        print(f"    - {col}")
    print(f"  Row count: {len(rows):,}")

    print("\n  --- first 3 rows verbatim ---")
    for i, row in enumerate(rows[:3], start=1):
        print(f"  row {i}:")
        for col in headers:
            print(f"    {col:<28s}  {row.get(col, '')}")

    title_col = _pick_title_column(headers, title_col_override)
    if not title_col:
        print("\n  WARNING: no obvious title/description column to filter on.")
        print(f"  Tried these candidates: {_TITLE_COL_CANDIDATES}")
        print("  Eyeball the columns above and re-run with --title-col <name>.")
        return

    hits = [r for r in rows if KEYWORDS.search(r.get(title_col, "") or "")]
    print(f"\n  Hurricane-keyword hits in `{title_col}`: {len(hits)}")
    for hit in hits[:10]:
        print("\n  hit:")
        for col in headers:
            print(f"    {col:<28s}  {hit.get(col, '')}")
    if len(hits) > 10:
        print(f"\n  ... and {len(hits) - 10} more")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe ForecastEx public CSV feeds for hurricane contracts.",
    )
    parser.add_argument("--prices-url", default=DEFAULT_PRICES_URL)
    parser.add_argument("--pairs-url", default=DEFAULT_PAIRS_URL)
    parser.add_argument(
        "--title-col",
        default=None,
        help="Override the column name to filter on (default: auto-detect).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    print("Probing ForecastEx public CSV feeds.")
    print(f"  Prices URL: {args.prices_url}")
    print(f"  Pairs URL:  {args.pairs_url}")
    print()
    print("If either URL 404s, find the real path on the forecastex.com/data")
    print("portal in a browser and re-run with --prices-url / --pairs-url.")

    with _client() as client:
        prices_status, prices_ctype, prices_body = _fetch(client, args.prices_url)
        pairs_status, pairs_ctype, pairs_body = _fetch(client, args.pairs_url)

    prices_headers, prices_rows = _parse_csv(prices_body)
    pairs_headers, pairs_rows = _parse_csv(pairs_body)

    _print_feed(
        "Prices CSV (daily close)",
        prices_status,
        prices_ctype,
        prices_body,
        prices_headers,
        prices_rows,
        args.title_col,
    )
    _print_feed(
        "Pairs CSV (~10-min refresh)",
        pairs_status,
        pairs_ctype,
        pairs_body,
        pairs_headers,
        pairs_rows,
        args.title_col,
    )

    print("\nDone. Paste the column listings and any hurricane hits back into")
    print("the project chat — Day 40 builds the real scraper from this.")

    # Exit non-zero only if both feeds completely failed, so cron / CI
    # would notice a total outage. Partial success returns 0.
    if not prices_headers and not pairs_headers:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
