"""Count-curve service — Kalshi hurricane-count threshold ladder as a curve.

Kalshi lists a series of binary contracts at incremental thresholds for the
annual Atlantic basin hurricane count: ``KXHURCTOT-{SEASON}DEC01-T{N}``,
each settling "Yes" if the final season total exceeds N hurricanes. Each
contract's market-clearing ``yes_price`` (0.0–1.0) reads as the market's
implied probability that total > N.

Pulling these threshold contracts into one curve gives readers a compact
view of the market's consensus distribution — where the median sits, how
much spread there is, how tail probabilities decay. Editorially: it's the
single visualization that translates a basket of binary contracts into a
"what does the market think the season will be" answer.

The curve is intentionally rendered raw — no monotonicity smoothing — so
off-season illiquidity (where mid-tail contracts trade infrequently and
their last-prints can violate the strict P(>N+1) ≤ P(>N) inequality)
remains visible. That's editorial honesty: thin pre-season liquidity is
itself a real feature of these markets.

Non-Kalshi count-style markets are out of scope for this service.
Polymarket runs hurricane-count contracts less consistently; if their
listing schema ever matches a stable pattern, add a second platform
branch here and tag each point with its source platform in the response.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rmn_dashboard.models import PredictionMarket

# Kalshi count-series ticker shape:
#   KXHURCTOT-{YEAR_2DIGIT}DEC01-T{N}
# where YEAR_2DIGIT is the two-digit season (26 for 2026) and N is the
# integer threshold ("more than N hurricanes"). DEC01 = December 1 close.
# The regex captures both so we can group by season and sort by N.
_KALSHI_COUNT_TICKER_PATTERN = re.compile(r"^KXHURCTOT-(\d{2})DEC01-T(\d+)$")

# Long-run Atlantic hurricane average (1991-2020 NOAA climatology). Used
# as a reference line on the rendered curve so readers can immediately
# compare market-implied median vs. the climate baseline. Kept as a
# constant rather than a runtime computation — this is editorial
# reference data, not derived signal.
_CLIMATE_AVERAGE_HURRICANES = 7.2

# Default season for the panel — the active Kalshi count series. Kept
# configurable so the curve can resolve a different season (e.g., a
# future panel that lets users toggle past seasons).
DEFAULT_SEASON = "26"  # 2026 → "26"


def compute_count_curve(
    db: Session,
    *,
    season: str = DEFAULT_SEASON,
) -> dict[str, Any]:
    """Return the Kalshi hurricane-count threshold curve for one season.

    Output shape::

        {
          "season":           "26",                  # two-digit year
          "season_label":     "2026",                # display year
          "platform":         "kalshi",
          "points": [
            {"threshold": 4,  "yes_price": 0.78},
            {"threshold": 5,  "yes_price": 0.47},
            ...
          ],
          "median":           4.9,                   # interpolated 50% crossover
          "climate_average":  7.2,                   # 1991-2020 reference
          "anomalies":        [{"threshold": 8, "yes_price": 0.22, "note": "violates monotonicity"}],
          "as_of":            "2026-05-13T18:30:00+00:00"
        }

    Empty ``points`` is honest: no count contracts in the DB yet, the
    season hasn't been listed, or the ingest hasn't run. The frontend
    renders an empty-state caption in that case rather than a malformed
    axis.
    """
    # Latest snapshot per ticker for the count series. Same group-by ->
    # join shape as services/markets.latest_hurricane_markets, but
    # scoped to KXHURCTOT-{season}DEC01-T% tickers specifically.
    like_pattern = f"KXHURCTOT-{season}DEC01-T%"
    latest_per_ticker = (
        select(
            PredictionMarket.ticker,
            func.max(PredictionMarket.last_updated).label("max_ts"),
        )
        .where(PredictionMarket.ticker.like(like_pattern))
        .group_by(PredictionMarket.ticker)
        .subquery()
    )
    stmt = select(PredictionMarket).join(
        latest_per_ticker,
        (PredictionMarket.ticker == latest_per_ticker.c.ticker)
        & (PredictionMarket.last_updated == latest_per_ticker.c.max_ts),
    )
    rows = list(db.scalars(stmt).all())

    points: list[dict[str, Any]] = []
    for row in rows:
        match = _KALSHI_COUNT_TICKER_PATTERN.match(row.ticker)
        if match is None:
            continue
        if row.yes_price is None:
            continue
        threshold = int(match.group(2))
        points.append(
            {
                "threshold": threshold,
                "yes_price": float(row.yes_price),
            }
        )

    # Sort ascending by threshold so the curve renders left-to-right
    # without the frontend needing to re-sort.
    points.sort(key=lambda p: p["threshold"])

    median = _interpolate_median(points)
    anomalies = _find_monotonicity_anomalies(points)
    as_of = _latest_as_of(rows)

    return {
        "season": season,
        "season_label": f"20{season}",
        "platform": "kalshi",
        "points": points,
        "median": median,
        "climate_average": _CLIMATE_AVERAGE_HURRICANES,
        "anomalies": anomalies,
        "as_of": as_of,
    }


def _interpolate_median(points: list[dict[str, Any]]) -> float | None:
    """Linear-interpolate the threshold N at which P(>N) crosses 50%.

    The curve descends with increasing N. Find the first pair of
    consecutive points where the upper has yes_price >= 0.5 and the
    lower has yes_price < 0.5 (or vice versa); linearly interpolate
    between them in N-space.

    Returns None when no crossover exists in the available data
    (e.g., all points above 50%, all below, or fewer than two points).
    """
    if len(points) < 2:
        return None
    for i in range(len(points) - 1):
        a = points[i]
        b = points[i + 1]
        # Descending curve: a.yes_price should be higher than b.yes_price.
        # 50% crossover sits between a and b iff one is >= 0.5 and the
        # other is < 0.5.
        a_above = a["yes_price"] >= 0.5
        b_above = b["yes_price"] >= 0.5
        if a_above == b_above:
            continue
        # Found the crossover bracket. Linear interpolation in
        # (threshold, yes_price) space.
        denom = a["yes_price"] - b["yes_price"]
        if denom == 0:
            # Degenerate: both equal but bracketing 0.5 was true above —
            # shouldn't happen given the inequality, but guard anyway.
            return float(a["threshold"])
        frac = (a["yes_price"] - 0.5) / denom
        median = a["threshold"] + frac * (b["threshold"] - a["threshold"])
        return round(median, 2)
    return None


def _find_monotonicity_anomalies(
    points: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flag points where yes_price violates the strict P(>N+1) ≤ P(>N) rule.

    Off-season, contracts at lower-volume thresholds can carry stale
    last-prints that produce a yes_price slightly above the prior
    point's. Mathematically impossible but operationally common.
    Surfaced here so the frontend can footnote them without having to
    re-derive the check client-side.
    """
    anomalies: list[dict[str, Any]] = []
    for i in range(1, len(points)):
        prev = points[i - 1]
        curr = points[i]
        if curr["yes_price"] > prev["yes_price"]:
            anomalies.append(
                {
                    "threshold": curr["threshold"],
                    "yes_price": curr["yes_price"],
                    "previous_threshold": prev["threshold"],
                    "previous_yes_price": prev["yes_price"],
                    "note": "violates monotonicity (off-season illiquidity likely)",
                }
            )
    return anomalies


def _latest_as_of(rows: list[PredictionMarket]) -> str | None:
    """Latest snapshot timestamp across the curve's contributing rows.

    Returns an ISO-8601 string for direct JSON consumption. None when
    no rows exist (off-season / fresh DB). Naive timestamps are tagged
    UTC before serialization to match the rest of the API's
    convention (see services/equity_quotes._isoformat for parallel).
    """
    if not rows:
        return None
    timestamps = [row.last_updated for row in rows if row.last_updated is not None]
    if not timestamps:
        return None
    latest = max(timestamps)
    if latest.tzinfo is None:
        from datetime import UTC

        latest = latest.replace(tzinfo=UTC)
    return latest.isoformat()


def count_series_ticker_prefix(season: str = DEFAULT_SEASON) -> str:
    """The ticker prefix the markets-list service uses to exclude count
    contracts from the supporting text list (since they're shown in the
    curve panel above). Kept here so the pattern stays in one place."""
    return f"KXHURCTOT-{season}DEC01-T"
