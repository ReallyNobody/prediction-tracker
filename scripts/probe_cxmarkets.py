#!/usr/bin/env python3
"""Probe CX Markets public Daily Activity Reports for hurricane landfall trades.

Day 43 — CX Markets equivalent of probe_polymarket.py /
probe_forecastex.py. Goal: discover the XLSX column shape before
writing the full ingest in Day 44.

CX Markets is a CFTC-regulated DCM (Designated Contract Market) under
FMX Futures Exchange — the Cantor Fitzgerald derivatives platform.
Unlike Kalshi (where hurricanes are a few tickers among hundreds) or
ForecastEx (no current hurricane contracts), CX's entire weather
offering is "Landfall Locations of Tropical Storms and Hurricanes."
Hurricane-first by design. Continuous operation 2018-2025.

Two public XLSX feeds, both unauthenticated:

  * Rolling 7-day aggregate (default):
      https://www.cxmarkets.com/reports/CX_WX_All_Trades.xlsx
  * Per-day report:
      https://cxmarkets.com/wp-content/uploads/YYYY/MM/WXYYYYMMDD.xlsx

What we want to know after a probe run:

  * Exact column headers in the XLSX (the public site doesn't publish
    a schema — we only know the file format and update cadence).
  * How "Landfall Location" contracts are encoded: binary per-state,
    multi-outcome by region, per-storm-per-state, etc.
  * Whether there's a stable contract identifier we can use as the
    ``ticker`` field on a PredictionMarket row.
  * What price + volume + open-interest fields exist (determines how
    cleanly CX slots into our existing PredictionMarket model).
  * Trade volume during season vs off-season — is the daily activity
    actually rich enough to surface in the dashboard's Risk Tape, or
    is it sparse like Etherisc / Eurex?

Strategy:

  1. Download the rolling 7-day aggregate (always available, fastest
     read on what the data actually looks like).
  2. Optionally a per-day file via ``--date`` to sample mid-season
     activity from past Atlantic seasons (Sept 2024 was active —
     Hurricane Helene landfall makes a good test date).
  3. Print column headers, row count, dtypes.
  4. Print first 5 rows verbatim.
  5. Tally any obvious category / contract-type column to see how the
     market is structured.

Usage
-----
    cd ~/Dev/Predict
    . .venv/bin/activate
    python scripts/probe_cxmarkets.py

    # Sample a specific historical mid-season day:
    python scripts/probe_cxmarkets.py --date 2024-09-26

    # Override default URL (e.g., if CX moves the aggregate file):
    python scripts/probe_cxmarkets.py --aggregate-url https://...

Read-only against the public CX feed. No DB writes. No auth. Safe to
run repeatedly. If the URLs 404, eyeball the live data page at
https://cxmarkets.com/rules-and-regulations/daily-activity-report/
and re-run with --aggregate-url / --date overrides.
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
from datetime import date as date_type

import httpx

try:
    import pandas as pd
except ImportError:  # pragma: no cover — pandas ships via yfinance already
    print(
        "ERROR: pandas not installed. Run: pip install pandas openpyxl",
        file=sys.stderr,
    )
    raise SystemExit(1) from None


AGGREGATE_URL = "https://www.cxmarkets.com/reports/CX_WX_All_Trades.xlsx"
PER_DAY_URL_TEMPLATE = (
    "https://cxmarkets.com/wp-content/uploads/{year}/{month:02d}/WX{date_compact}.xlsx"
)

HTTP_TIMEOUT = 60.0  # XLSX downloads can be a few hundred KB; generous timeout.


def _client() -> httpx.Client:
    """HTTPx client with a courteous User-Agent. CX is a small exchange and
    we want to be identifiable in their access logs if our usage misbehaves."""
    return httpx.Client(
        timeout=HTTP_TIMEOUT,
        headers={
            "User-Agent": "Risk Market News probe (research@riskmarketnews.com)",
            "Accept": (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, "
                "application/octet-stream, */*"
            ),
        },
        follow_redirects=True,
    )


def _per_day_url(target_date: date_type) -> str:
    """Build the per-day XLSX URL.

    The discovered pattern is ``WX<YYYYMMDD>.xlsx`` under
    ``wp-content/uploads/<YYYY>/<MM>/`` (zero-padded month). Verified
    by hand against the live Daily Activity Report listing.
    """
    return PER_DAY_URL_TEMPLATE.format(
        year=target_date.year,
        month=target_date.month,
        date_compact=target_date.strftime("%Y%m%d"),
    )


def _download(client: httpx.Client, url: str) -> tuple[int, str, bytes]:
    """Download a URL. Returns (status_code, content_type, body_bytes).
    Doesn't raise — diagnostic mode, we want to see what came back even
    when the URL is unexpectedly redirected or returns the WordPress
    404 HTML page instead of an XLSX."""
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        print(f"  ERROR: {url} → {exc}", file=sys.stderr)
        return (0, "", b"")
    return (
        response.status_code,
        response.headers.get("content-type", ""),
        response.content,
    )


def _parse_xlsx(body: bytes) -> pd.DataFrame | None:
    """Parse XLSX body bytes into a DataFrame. Returns None if the body
    isn't a valid spreadsheet (e.g., we got an HTML 404 page back)."""
    if not body or len(body) < 32:
        return None
    # XLSX files start with the ZIP magic ``PK\x03\x04``. If we got
    # HTML or a redirect page, the content won't match.
    if body[:4] != b"PK\x03\x04":
        return None
    try:
        return pd.read_excel(io.BytesIO(body), engine="openpyxl")
    except Exception as exc:  # noqa: BLE001 — diagnostic path
        print(f"  ERROR: pandas failed to parse XLSX: {exc}", file=sys.stderr)
        return None


def _print_feed(label: str, status: int, ctype: str, body: bytes, df: pd.DataFrame | None) -> None:
    """Print a one-feed diagnostic block."""
    print(f"\n=== {label} ===")
    print(f"  HTTP status:  {status}")
    print(f"  Content-Type: {ctype}")
    print(f"  Body bytes:   {len(body):,}")

    if df is None:
        if not body:
            print("  (empty body — URL probably 404'd or redirected)")
        elif body[:4] != b"PK\x03\x04":
            preview = body[:300].decode("utf-8", errors="replace")
            print(f"  (body not a ZIP/XLSX — first 300 chars: {preview!r})")
        else:
            print("  (XLSX body received but pandas failed to parse — see error above)")
        return

    print(f"  Rows:         {len(df):,}")
    print(f"  Columns ({len(df.columns)}):")
    for col, dtype in zip(df.columns, df.dtypes, strict=True):
        print(f"    - {col!s:<32s}  ({dtype!s})")

    print("\n  --- first 5 rows verbatim ---")
    # ``to_string()`` keeps wide columns intact; truncate is acceptable
    # since the operator can re-run with a query if a column is unclear.
    with pd.option_context(
        "display.max_columns", None, "display.width", 200, "display.max_colwidth", 60
    ):
        print(df.head(5).to_string(index=False))

    # Try to surface a "what kinds of contracts?" tally if there's an
    # obvious categorical column. We probe a few common candidates;
    # this is heuristic, the operator can re-derive once they see the
    # actual shape.
    _print_categorical_tallies(df)

    # Volume signal. Several common column names for traded quantity.
    _print_volume_summary(df)


def _print_categorical_tallies(df: pd.DataFrame) -> None:
    """Print value counts for any plausibly-categorical column so the
    operator can see how the contract universe is partitioned (per-state,
    per-storm, per-region, etc.)."""
    candidates = [
        "Contract",
        "ContractName",
        "Contract Name",
        "ContractType",
        "Symbol",
        "Ticker",
        "ProductCode",
        "Product",
        "Region",
        "State",
        "LandfallRegion",
        "Type",
        "Category",
    ]
    matches = [c for c in candidates if c in df.columns]
    if not matches:
        return
    print("\n  --- value counts on likely categorical columns ---")
    for col in matches:
        try:
            counts = df[col].value_counts(dropna=False).head(15)
        except Exception:  # noqa: BLE001
            continue
        print(f"\n  {col} (top 15):")
        for value, n in counts.items():
            print(f"    {n:>5d}  {value}")


def _print_volume_summary(df: pd.DataFrame) -> None:
    """Sum any plausibly-numeric volume / quantity / size column. Tells us
    whether the day's trading activity is rich enough to be worth ingesting
    or sparse enough to deprioritize."""
    candidates = [
        "Quantity",
        "Qty",
        "Volume",
        "Size",
        "Contracts",
        "TradeQuantity",
        "ExecutedQuantity",
    ]
    matches = [c for c in candidates if c in df.columns]
    if not matches:
        return
    print("\n  --- numeric volume column summary ---")
    for col in matches:
        try:
            series = pd.to_numeric(df[col], errors="coerce").dropna()
        except Exception:  # noqa: BLE001
            continue
        if series.empty:
            continue
        print(
            f"  {col}: count={len(series):,}  "
            f"sum={series.sum():,.0f}  mean={series.mean():.2f}  "
            f"min={series.min():.0f}  max={series.max():.0f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe CX Markets public XLSX feeds for landfall trades.",
    )
    parser.add_argument(
        "--aggregate-url",
        default=AGGREGATE_URL,
        help=f"Rolling 7-day aggregate URL (default: {AGGREGATE_URL}).",
    )
    parser.add_argument(
        "--date",
        default=None,
        help=(
            "Optional per-day date to sample (YYYY-MM-DD). "
            "Helene mid-storm 2024-09-26 is a good test date for an "
            "active-season day."
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    print("Probing CX Markets public XLSX feeds.")
    print(f"  Aggregate URL: {args.aggregate_url}")
    if args.date:
        target = date_type.fromisoformat(args.date)
        per_day_url = _per_day_url(target)
        print(f"  Per-day URL:   {per_day_url} (date={args.date})")
    else:
        per_day_url = None
        print("  Per-day URL:   (skipped — pass --date YYYY-MM-DD to sample)")

    with _client() as client:
        agg_status, agg_ctype, agg_body = _download(client, args.aggregate_url)
        agg_df = _parse_xlsx(agg_body)
        _print_feed("Rolling 7-day aggregate", agg_status, agg_ctype, agg_body, agg_df)

        if per_day_url is not None:
            day_status, day_ctype, day_body = _download(client, per_day_url)
            day_df = _parse_xlsx(day_body)
            _print_feed(
                f"Per-day report ({args.date})",
                day_status,
                day_ctype,
                day_body,
                day_df,
            )

    print("\nDone. Paste column listings + sample rows back into the project")
    print("chat — Day 44 builds the cxmarkets scraper from this output.")

    # Non-zero exit only if the aggregate fetch completely failed; we want
    # cron to notice an outage but tolerate per-day misses (off-season, the
    # per-day file may not exist for a given date).
    if agg_df is None:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
