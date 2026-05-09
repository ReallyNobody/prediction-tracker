#!/usr/bin/env python3
"""Watch ForecastEx for hurricane contracts and alert when they appear.

ForecastEx (CFTC-regulated DCM owned by Interactive Brokers) doesn't
currently list hurricane contracts — its Environmental category is
city-temperature and rainfall. But IBKR has marketed hurricane
contracts and we expect them to relist seasonally as the Atlantic
basin warms up (June 1 - Nov 30).

This script is the cheap way to find out *when* without checking
manually. Run it weekly via cron; it stays silent unless something
new appears, at which point it prints a report and exits non-zero so
cron's mailto fires.

Behavior
--------
  1. Fetch yesterday's Summary CSV from forecastex.com (today's isn't
     ready until end-of-day Eastern).
  2. Filter ``product_name`` for hurricane-adjacent keywords (same
     regex family as probe_forecastex.py / probe_polymarket.py).
  3. Diff against a JSON state file of previously-alerted product_ids
     so we only shout the *first* time a contract appears.
  4. Exit 0 (silent) on no change. Exit 1 (with stdout) on new hits.
     Exit 2 on fetch failure — distinguishable from a normal alert.

Usage
-----
    python scripts/monitor_forecastex.py
    python scripts/monitor_forecastex.py --date 2026-06-15
    python scripts/monitor_forecastex.py --state /tmp/test-state.json

Cron example — Mondays at 9am, emailing Chris on a real alert:

    MAILTO=christopher.westfall@gmail.com
    0 9 * * MON cd /Users/chriswestfall/dev/Predict && \\
        .venv/bin/python scripts/monitor_forecastex.py

Read-only against the public ForecastEx data feed. No DB writes.
Safe to run repeatedly — the state file ensures idempotent alerting.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

DOWNLOAD_URL = "https://forecastex.com/api/download"
HTTP_TIMEOUT = 30.0

# Hurricane-adjacent keywords. Same regex family as the probe scripts
# but with "named storm" added — that's the most likely product-name
# wording ForecastEx would use for individual storm contracts based on
# how Kalshi names theirs (e.g., "Will a named storm form by ...").
# Deliberately omits a bare "storm" — pulls in winter weather.
KEYWORDS = re.compile(
    r"\b("
    r"hurricane|tropical|cyclone|landfall|"
    r"atlantic\s+(?:basin|season|hurricane)|"
    r"named\s+storm"
    r")\b",
    re.IGNORECASE,
)

DEFAULT_STATE_PATH = Path.home() / ".forecastex_seen.json"


def _fetch_summary(target_date: date) -> str:
    """Fetch the Summary CSV for a given date. Raises on HTTP error."""
    params = {"type": "summary", "date": target_date.strftime("%Y%m%d")}
    headers = {
        # Same identifier pattern as our other scrapers — gives
        # ForecastEx a way to contact us if they object to the traffic.
        "User-Agent": "Risk Market News monitor (research@riskmarketnews.com)",
        "Accept": "text/csv, */*",
    }
    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        response = client.get(DOWNLOAD_URL, params=params, headers=headers)
        response.raise_for_status()
        return response.text


def _hurricane_hits(body: str) -> list[dict[str, str]]:
    """Parse Summary CSV and return rows whose product_name matches the
    hurricane regex. Tolerates a leading BOM and empty input."""
    if not body.strip():
        return []
    if body.startswith("﻿"):
        body = body[1:]
    reader = csv.DictReader(io.StringIO(body))
    return [r for r in reader if KEYWORDS.search(r.get("product_name", "") or "")]


def _load_state(path: Path) -> set[str]:
    """Load the set of previously-alerted product_ids. Returns empty
    set on missing or corrupt state file — better to over-alert once
    than to silently fail forever."""
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: state file unreadable ({exc}); treating as empty.", file=sys.stderr)
        return set()
    seen = data.get("seen_product_ids", [])
    return set(seen) if isinstance(seen, list) else set()


def _save_state(path: Path, seen: set[str]) -> None:
    """Persist the set of alerted product_ids."""
    path.write_text(json.dumps({"seen_product_ids": sorted(seen)}, indent=2))


def _format_alert(target_date: date, hits: list[dict[str, str]], new_ids: set[str]) -> str:
    """Build the human-readable alert body."""
    lines = [
        f"NEW ForecastEx hurricane contract(s) appeared for {target_date}:",
        "",
    ]
    for hit in hits:
        if hit["product_id"] in new_ids:
            lines.append(
                f"  {hit['product_id']:<8s}  "
                f"[{hit.get('product_category', '?')}]  "
                f"{hit['product_name']}  "
                f"(total_pairs={hit.get('total_pairs', '?')})"
            )
    lines.append("")
    lines.append("All matching contracts (incl. previously seen):")
    for hit in hits:
        marker = "*" if hit["product_id"] in new_ids else " "
        lines.append(
            f"  {marker} {hit['product_id']:<8s}  {hit['product_name']}  "
            f"(pairs={hit.get('total_pairs', '?')})"
        )
    lines += [
        "",
        "Investigate at https://forecastex.com/markets — if these look",
        "integrate-worthy, the Day 39 probe + Day 40 scraper plan from",
        "project chat is ready to execute.",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Alert when ForecastEx lists new hurricane contracts.",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Date to fetch in YYYY-MM-DD form (default: yesterday).",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help=f"JSON state file (default: {DEFAULT_STATE_PATH}).",
    )
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date) if args.date else date.today() - timedelta(days=1)

    try:
        body = _fetch_summary(target_date)
    except httpx.HTTPError as exc:
        # Distinct exit code so cron / mailto can tell a fetch failure
        # apart from a real alert. Print to stderr so cron's stdout
        # capture doesn't conflate the two.
        print(f"ERROR fetching ForecastEx summary for {target_date}: {exc}", file=sys.stderr)
        return 2

    hits = _hurricane_hits(body)
    if not hits:
        # No hurricane contracts at all. Silent — cron stays quiet.
        return 0

    seen = _load_state(args.state)
    current_ids = {h["product_id"] for h in hits}
    new_ids = current_ids - seen

    if not new_ids:
        # All matching contracts already alerted previously. Silent.
        return 0

    # New contracts found — print the alert and persist state so we
    # don't re-alert next week.
    print(_format_alert(target_date, hits, new_ids))
    _save_state(args.state, seen | current_ids)

    # Non-zero exit so cron's MAILTO fires.
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
