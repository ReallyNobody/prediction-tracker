"""Daily change rollup — Panel 6's "What changed today" view.

Pure derived data over what we already persist. Three source tables,
three categories of change:

  * StormObservation — intensity / classification deltas over the
    last 24 hours per active storm.
  * TickerQuote — biggest absolute change_percent movers from the
    latest yfinance scrape (which already represents day-over-day
    against yfinance's previous_close).
  * TickerQuote — the cat_bond_etf row's change_percent, called out
    separately because it carries different editorial weight than a
    single insurer move.

Editorial principle: every line is a single human-readable headline.
The reader scans the panel in three seconds and knows what shifted.
We do *not* dump raw deltas — we narrate them. When nothing notable
is happening, the panel says so honestly.

Intentionally NOT included today:

  * Prediction-market price shifts. The Kalshi snapshot history isn't
    deep enough yet (Day 7 onward) to compute reliable 24h deltas,
    and the mid-spring market is too quiet to make noise about. Add
    in Week 5 if it's earning its space.
  * "New advisories." Largely redundant with storm intensity changes;
    when an advisory triggers a notable forecast shift, it shows up
    in the storm row. Adding it again would inflate the panel.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rmn_dashboard.data.universe import load_universe
from rmn_dashboard.models import Storm, StormObservation
from rmn_dashboard.services.equity_quotes import latest_universe_quotes

# Minimum gap between "latest" and "prior" observations for the storm
# delta query. The 18h floor (vs. exactly 24h) tolerates slight cadence
# variance and weekend gaps in NHC polling without losing legitimate
# yesterday-vs-today comparisons.
_PRIOR_OBSERVATION_FLOOR_HOURS = 18

# Cap on equity movers shown. Three is the editorially right number —
# enough to give a sense of the day's mix, few enough to scan.
_EQUITY_MOVERS_LIMIT = 3


def todays_changes(db: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """Return a structured payload of day-over-day changes.

    Shape::

        {
          "as_of":   "2026-04-25T17:30:00+00:00",
          "storms":  [ {kind, name, headline}, ... ],
          "equities":[ {ticker, name, sector, headline, change_percent}, ... ],
          "cat_bond":{ ticker, name, headline, change_percent } | None,
        }

    The ``now`` kwarg is injectable for deterministic testing.
    Production callers leave it None; we stamp ``datetime.now(UTC)``.

    Lists are always present (possibly empty); ``cat_bond`` is None
    when the universe has no cat_bond_etf entry or when no quote
    exists for it yet.
    """
    if now is None:
        now = datetime.now(UTC)

    return {
        "as_of": now.isoformat(),
        "storms": _storm_changes(db, now=now),
        "equities": _equity_movers(db, limit=_EQUITY_MOVERS_LIMIT),
        "cat_bond": _cat_bond_change(db),
    }


# ----- Storm intensity / classification deltas ----------------------------


def _storm_changes(db: Session, *, now: datetime) -> list[dict[str, Any]]:
    """Per-active-storm 24h delta line.

    For each active storm we pull the latest observation and the most
    recent observation that's at least 18 hours older. If the prior
    observation is missing (newly tracked storm, or a quiet scrape
    history), the line says "newly tracked" instead of a delta.

    The prior-observation lookup is deliberately NOT anchored to ``now``.
    What we want is "did this storm change since yesterday relative to
    its own latest reading," and yesterday-relative-to-latest is the
    correct comparison whether the storm's data is fresh or whether
    it's a historical seed (Irma 2017, Ian 2022) loaded for dev. The
    18h floor between latest and prior is the only timing constraint
    that matters here.
    """
    # ``now`` is retained as a function parameter for symmetry with
    # the rest of the service and to keep tests deterministic.
    _ = now

    active_storm_ids = list(db.scalars(select(Storm.id).where(Storm.status == "active")).all())
    if not active_storm_ids:
        return []

    headlines: list[dict[str, Any]] = []
    for storm_id in active_storm_ids:
        latest = db.scalar(
            select(StormObservation)
            .where(StormObservation.storm_id == storm_id)
            .order_by(StormObservation.observation_time.desc())
            .limit(1)
        )
        if latest is None:
            continue

        # "Yesterday" reference: most recent observation at least
        # _PRIOR_OBSERVATION_FLOOR_HOURS before the latest.
        prior_threshold = latest.observation_time - timedelta(hours=_PRIOR_OBSERVATION_FLOOR_HOURS)
        prior = db.scalar(
            select(StormObservation)
            .where(StormObservation.storm_id == storm_id)
            .where(StormObservation.observation_time <= prior_threshold)
            .order_by(StormObservation.observation_time.desc())
            .limit(1)
        )
        headline = _storm_headline(latest, prior)
        if headline is None:
            continue
        headlines.append(headline)

    return headlines


def _storm_headline(
    latest: StormObservation, prior: StormObservation | None
) -> dict[str, Any] | None:
    """Render a single storm-change line, or None if nothing changed."""
    storm_name = latest.storm.name or latest.storm.nhc_id or "Unnamed system"
    if prior is None:
        return {
            "kind": "new",
            "name": storm_name,
            "headline": (
                f"{storm_name} newly tracked — {latest.classification} at {latest.intensity_kt} kt."
            ),
        }

    delta_kt = latest.intensity_kt - prior.intensity_kt
    if abs(delta_kt) < 5 and latest.classification == prior.classification:
        # Nothing notable — skip the line so the panel doesn't read as
        # "5 storms · all unchanged" noise.
        return None

    if latest.classification != prior.classification:
        kind = "reclassified"
        headline = (
            f"{storm_name} {prior.classification} → {latest.classification} "
            f"({prior.intensity_kt} → {latest.intensity_kt} kt)."
        )
    elif delta_kt > 0:
        kind = "intensified"
        headline = f"{storm_name} intensified +{delta_kt} kt to {latest.intensity_kt} kt."
    else:
        kind = "weakened"
        headline = f"{storm_name} weakened {delta_kt} kt to {latest.intensity_kt} kt."
    return {"kind": kind, "name": storm_name, "headline": headline}


# ----- Equity movers ------------------------------------------------------


def _equity_movers(
    db: Session, *, limit: int, sectors: Iterable[str] | None = None
) -> list[dict[str, Any]]:
    """Top |change_percent| movers in the equity universe.

    Defaults to the four equity sectors (insurer / reinsurer /
    homebuilder / utility) — cat_bond_etf is reported separately by
    ``_cat_bond_change`` so the cat bond signal doesn't get drowned
    under a noisy single-name move.
    """
    if sectors is None:
        sectors = ["insurer", "reinsurer", "homebuilder", "utility"]

    rows = latest_universe_quotes(db, sectors=list(sectors))
    movers: list[dict[str, Any]] = []
    for row in rows:
        quote = row.get("quote")
        if not quote:
            continue
        change_pct = quote.get("change_percent")
        if change_pct is None:
            continue
        movers.append(
            {
                "ticker": row["ticker"],
                "name": row["name"],
                "sector": row["sector"],
                "change_percent": change_pct,
                "headline": _equity_headline(row["ticker"], row["name"], change_pct),
            }
        )

    movers.sort(key=lambda m: abs(m["change_percent"]), reverse=True)
    return movers[:limit]


def _equity_headline(ticker: str, name: str, change_pct: float) -> str:
    """One-line headline for an equity mover.

    Format: 'UVE +4.2% — Universal Insurance Holdings'. Caller renders
    +/- and color via the change_percent value; we keep the headline
    text neutral so the JS controls the visual treatment.
    """
    sign = "+" if change_pct >= 0 else ""
    return f"{ticker} {sign}{change_pct:.2f}% — {name}"


# ----- Cat bond proxy -----------------------------------------------------


def _cat_bond_change(db: Session) -> dict[str, Any] | None:
    """Latest cat_bond_etf row's day-over-day change.

    Returns None when the universe has no cat_bond_etf entry, or when
    one exists but has no quote yet.
    """
    universe = load_universe()
    cat_bond_tickers = {e.ticker for e in universe.tickers if e.sector == "cat_bond_etf"}
    if not cat_bond_tickers:
        return None

    rows = latest_universe_quotes(db, sectors=["cat_bond_etf"])
    for row in rows:
        quote = row.get("quote")
        if not quote:
            continue
        change_pct = quote.get("change_percent")
        if change_pct is None:
            continue
        sign = "+" if change_pct >= 0 else ""
        return {
            "ticker": row["ticker"],
            "name": row["name"],
            "change_percent": change_pct,
            "headline": (f"{row['ticker']} {sign}{change_pct:.2f}% — cat bond proxy."),
        }
    return None
