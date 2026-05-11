"""Signal Tape — the page-top hurricane risk anchor (Day 43).

Synthesizes four data layers into a single horizontal band:

    Storms   |  Equities   |  Risk capital  |  Markets

Each cell carries:
  * a current "tier" (quiet / watching / active / severe),
  * a short value word + a one-line driver (the data point behind the tier),
  * a 14-day daily-aggregate history for the sparkline.

A composite "tone" word — same four-tier scale — is the worst-case state
across the four cells. The tone is *labeling only*: it doesn't add
information beyond what the cells already show. A reader who wants to
know why the tone is "Watching" can look at which cell is in that tier.

Editorial design notes:

  * No composite single-number index. Collapsing four heterogeneous
    signals into one number reads as objective when it's not.
  * Downside-only escalation for equities and risk capital. A 4%
    insurer rally isn't editorially "Severe" hurricane risk —
    upside moves stay Quiet by design.
  * Pre-launch thresholds are conservative best-guesses (off-season
    "Quiet" and major-landfall "Severe" both read correctly; middle
    tiers will need tuning against in-season operational data).

The history-array depth is whatever snapshot data the DB has accumulated.
At launch some series have weeks of points; some have days. The frontend
renders honestly — a sparkline with 3 points is a sparkline with 3 points.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rmn_dashboard.data.universe import load_universe
from rmn_dashboard.models import (
    PredictionMarket,
    Storm,
    StormObservation,
    TickerQuote,
)

Tier = Literal["quiet", "watching", "active", "severe"]

# Tier order from least to most concerning. The composite tone is the
# max-tier across the four cells; this list defines what "max" means.
_TIER_ORDER: tuple[Tier, ...] = ("quiet", "watching", "active", "severe")
_TIER_RANK: dict[Tier, int] = {t: i for i, t in enumerate(_TIER_ORDER)}

# Display labels — title-cased in the JSON payload so the frontend can
# render them verbatim without re-mapping.
_TIER_LABEL: dict[Tier, str] = {
    "quiet": "Quiet",
    "watching": "Watching",
    "active": "Active",
    "severe": "Severe",
}

# How far back the history sparkline reaches. 14 calendar days is the
# default — enough to show a 2-week ramp during basin spin-up, short
# enough that a single-storm event still dominates the chart.
DEFAULT_HISTORY_DAYS = 14

# Sectors that flow into the "Equities" cell's aggregate pressure
# calculation. Insurers, utilities, and LNG are operationally exposed
# to landfall in a way that homebuilders and reinsurers aren't (the
# latter two are real exposure stories but their daily-move signal is
# noisier and conflates with broader market beta).
_EQUITIES_PRESSURE_SECTORS: frozenset[str] = frozenset({"insurer", "utility", "lng"})

# The benchmark we subtract from equities pressure to isolate hurricane-
# specific moves from broad sector sentiment. Matches the vs-XLU spread
# convention from Panel 2 (Day 40).
_BENCHMARK_TICKER = "XLU"

# Cat-bond proxy ticker used for the "Risk capital" cell. Matches the
# Panel 3 single-source convention.
_CAT_BOND_TICKER = "ILS"

# Hurricane-keyword markets flow through PredictionMarket.category as
# set by the Kalshi + Polymarket ingest tasks. Same filter for both
# platforms.
_HURRICANE_CATEGORY = "hurricane"


# --- Cell builders --------------------------------------------------------


def _storms_cell(db: Session, *, history_days: int) -> dict[str, Any]:
    """Active-storms cell. Tier from current count + peak intensity.

    Quiet = 0 active. Watching = at least one tropical depression /
    storm. Active = at least one hurricane (>=64 kt). Severe = at
    least one Cat 3+ (>=100 kt).
    """
    # Most-recent observation per active storm. Storm.status is the
    # editorial lifecycle flag the NHC ingest maintains; "active" is
    # the in-season state, distinct from "post-tropical" / "dissipated".
    active_storms = list(db.scalars(select(Storm).where(Storm.status == "active")).all())

    count = len(active_storms)
    max_kt: int | None = None
    headline_name: str | None = None
    if active_storms:
        # Pull latest observation per storm to get current intensity.
        for storm in active_storms:
            obs = db.scalars(
                select(StormObservation)
                .where(StormObservation.storm_id == storm.id)
                .order_by(StormObservation.observation_time.desc())
                .limit(1),
            ).first()
            kt = obs.intensity_kt if obs is not None else (storm.max_wind_kt or 0)
            if max_kt is None or kt > max_kt:
                max_kt = kt
                headline_name = storm.name

    if count == 0:
        tier: Tier = "quiet"
        value = "No active storms"
        driver = "Basin is clear."
    elif (max_kt or 0) >= 100:
        tier = "severe"
        value = f"{count} active"
        driver = f"{headline_name} · {max_kt} kt (Cat 3+)"
    elif (max_kt or 0) >= 64:
        tier = "active"
        value = f"{count} active"
        driver = f"{headline_name} · {max_kt} kt"
    else:
        tier = "watching"
        value = f"{count} active"
        driver = f"{headline_name} · {max_kt or 0} kt"

    history = _storms_history(db, days=history_days)

    return {
        "label": "Storms",
        "tier": tier,
        "tier_label": _TIER_LABEL[tier],
        "value": value,
        "driver": driver,
        "history": history,
    }


def _storms_history(db: Session, *, days: int) -> list[dict[str, Any]]:
    """Daily count of distinct active storms over the past `days`.

    We count distinct storm_id per observation date — captures the
    spin-up shape of an active basin (1 → 2 → 3 storms tracking
    simultaneously) rather than just intensity.
    """
    start = datetime.now(UTC) - timedelta(days=days)
    rows = db.execute(
        select(
            func.date(StormObservation.observation_time).label("day"),
            func.count(func.distinct(StormObservation.storm_id)).label("n_storms"),
        )
        .where(StormObservation.observation_time >= start)
        .group_by(func.date(StormObservation.observation_time))
        .order_by(func.date(StormObservation.observation_time)),
    ).all()
    return [{"date": _date_iso(row.day), "value": float(row.n_storms)} for row in rows]


def _equities_cell(db: Session, *, history_days: int) -> dict[str, Any]:
    """Equity sector pressure cell. Aggregate change_percent of the
    operationally-exposed sectors (insurer / utility / lng) minus XLU.

    Downside-only escalation: spreads above zero map to Quiet by
    editorial choice. A 3% insurer rally is good news, not a hurricane
    risk signal.
    """
    universe = load_universe()
    pressure_tickers = [
        e.ticker for e in universe.tickers if e.sector in _EQUITIES_PRESSURE_SECTORS
    ]
    if not pressure_tickers:
        return _empty_cell("Equities", "No tickers yet")

    # Latest quote per pressure ticker + XLU.
    quotes = _latest_quotes_for(db, [*pressure_tickers, _BENCHMARK_TICKER])
    xlu_quote = quotes.get(_BENCHMARK_TICKER)
    xlu_change = xlu_quote.change_percent if xlu_quote is not None else None

    pressure_changes = [
        q.change_percent
        for t in pressure_tickers
        if (q := quotes.get(t)) is not None and q.change_percent is not None
    ]
    if not pressure_changes:
        return _empty_cell("Equities", "No quotes yet")

    avg_change = sum(pressure_changes) / len(pressure_changes)
    spread = avg_change - xlu_change if xlu_change is not None else avg_change

    # Downside-only escalation. Positive spread → Quiet.
    if spread <= -4.0:
        tier: Tier = "severe"
    elif spread <= -2.0:
        tier = "active"
    elif spread <= -0.5:
        tier = "watching"
    else:
        tier = "quiet"

    sign = "+" if spread >= 0 else ""
    value = "Pressure" if tier != "quiet" else "Calm"
    driver = f"Sector {sign}{avg_change:.1f}% · vs XLU {sign}{spread:.1f}%"

    history = _equities_history(db, days=history_days, tickers=pressure_tickers)

    return {
        "label": "Equities",
        "tier": tier,
        "tier_label": _TIER_LABEL[tier],
        "value": value,
        "driver": driver,
        "history": history,
    }


def _equities_history(
    db: Session,
    *,
    days: int,
    tickers: list[str],
) -> list[dict[str, Any]]:
    """Daily average change_percent across the pressure tickers.

    Uses snapshot-shaped TickerQuote rows; aggregates per date.
    """
    start = datetime.now(UTC) - timedelta(days=days)
    rows = db.execute(
        select(
            func.date(TickerQuote.as_of).label("day"),
            func.avg(TickerQuote.change_percent).label("avg_change"),
        )
        .where(TickerQuote.ticker.in_(tickers))
        .where(TickerQuote.as_of >= start)
        .where(TickerQuote.change_percent.is_not(None))
        .group_by(func.date(TickerQuote.as_of))
        .order_by(func.date(TickerQuote.as_of)),
    ).all()
    return [{"date": _date_iso(row.day), "value": float(row.avg_change or 0.0)} for row in rows]


def _risk_capital_cell(db: Session, *, history_days: int) -> dict[str, Any]:
    """Risk-capital cell. Driven by the ILS cat-bond proxy: downside
    moves indicate cat bond repricing stress, which historically
    precedes broader reinsurance market firming.

    Thresholds tighter than equities — cat bond ETFs have lower
    daily volatility than individual equity sectors, so a 1.5% ILS
    drawdown is editorially meaningful.
    """
    quotes = _latest_quotes_for(db, [_CAT_BOND_TICKER])
    ils = quotes.get(_CAT_BOND_TICKER)
    if ils is None or ils.change_percent is None:
        return _empty_cell("Risk capital", "No ILS quote yet")

    change = ils.change_percent

    if change <= -3.0:
        tier: Tier = "severe"
    elif change <= -1.5:
        tier = "active"
    elif change <= -0.5:
        tier = "watching"
    else:
        tier = "quiet"

    sign = "+" if change >= 0 else ""
    value = "Stress" if tier != "quiet" else "Calm"
    driver = f"ILS {sign}{change:.1f}%"

    history = _risk_capital_history(db, days=history_days)

    return {
        "label": "Risk capital",
        "tier": tier,
        "tier_label": _TIER_LABEL[tier],
        "value": value,
        "driver": driver,
        "history": history,
    }


def _risk_capital_history(db: Session, *, days: int) -> list[dict[str, Any]]:
    """Daily change_percent for the ILS cat-bond ETF."""
    start = datetime.now(UTC) - timedelta(days=days)
    rows = db.execute(
        select(
            func.date(TickerQuote.as_of).label("day"),
            func.avg(TickerQuote.change_percent).label("avg_change"),
        )
        .where(TickerQuote.ticker == _CAT_BOND_TICKER)
        .where(TickerQuote.as_of >= start)
        .where(TickerQuote.change_percent.is_not(None))
        .group_by(func.date(TickerQuote.as_of))
        .order_by(func.date(TickerQuote.as_of)),
    ).all()
    return [{"date": _date_iso(row.day), "value": float(row.avg_change or 0.0)} for row in rows]


def _markets_cell(db: Session, *, history_days: int) -> dict[str, Any]:
    """Prediction-market cell. Aggregate hurricane-keyword volume_24h
    across all platforms (Kalshi + Polymarket + future additions).

    Pre-launch thresholds are absolute dollar amounts (we don't have
    enough operational history to baseline against a rolling average
    yet). In Phase 2 these become multiples-of-trailing-average.
    """
    # Latest snapshot per ticker — dedupe via max(last_updated) per
    # (platform, ticker), summed.
    sub = (
        select(
            PredictionMarket.platform,
            PredictionMarket.ticker,
            func.max(PredictionMarket.last_updated).label("max_ts"),
        )
        .where(PredictionMarket.category == _HURRICANE_CATEGORY)
        .group_by(PredictionMarket.platform, PredictionMarket.ticker)
        .subquery()
    )
    latest_rows = (
        db.execute(
            select(PredictionMarket).join(
                sub,
                (PredictionMarket.platform == sub.c.platform)
                & (PredictionMarket.ticker == sub.c.ticker)
                & (PredictionMarket.last_updated == sub.c.max_ts),
            ),
        )
        .scalars()
        .all()
    )

    total_volume = sum((row.volume_24h or 0.0) for row in latest_rows)

    if total_volume >= 20_000:
        tier: Tier = "severe"
    elif total_volume >= 5_000:
        tier = "active"
    elif total_volume >= 1_000:
        tier = "watching"
    else:
        tier = "quiet"

    # Headline driver — the single biggest mover.
    if latest_rows:
        biggest = max(latest_rows, key=lambda r: r.volume_24h or 0.0)
        platform_label = (biggest.platform or "?").capitalize()
        driver = f"${total_volume:,.0f} 24h · top {platform_label}"
    else:
        driver = "No markets yet"

    value = "Elevated" if tier != "quiet" else "Quiet"

    history = _markets_history(db, days=history_days)

    return {
        "label": "Markets",
        "tier": tier,
        "tier_label": _TIER_LABEL[tier],
        "value": value,
        "driver": driver,
        "history": history,
    }


def _markets_history(db: Session, *, days: int) -> list[dict[str, Any]]:
    """Daily aggregate hurricane-keyword volume_24h.

    Volume_24h is rolling, so per-day aggregation takes the MAX per
    (ticker, day) to dedupe snapshot duplicates, then SUMS across
    tickers. Approximates "what was the total hurricane-market 24h
    volume on this day."
    """
    start = datetime.now(UTC) - timedelta(days=days)
    # Inner: max volume_24h per (platform, ticker, day)
    inner = (
        select(
            func.date(PredictionMarket.last_updated).label("day"),
            PredictionMarket.platform,
            PredictionMarket.ticker,
            func.max(PredictionMarket.volume_24h).label("vol"),
        )
        .where(PredictionMarket.category == _HURRICANE_CATEGORY)
        .where(PredictionMarket.last_updated >= start)
        .where(PredictionMarket.volume_24h.is_not(None))
        .group_by(
            func.date(PredictionMarket.last_updated),
            PredictionMarket.platform,
            PredictionMarket.ticker,
        )
        .subquery()
    )
    # Outer: sum across (platform, ticker) per day
    rows = db.execute(
        select(inner.c.day, func.sum(inner.c.vol).label("total_vol"))
        .group_by(inner.c.day)
        .order_by(inner.c.day),
    ).all()
    return [{"date": _date_iso(row.day), "value": float(row.total_vol or 0.0)} for row in rows]


# --- Compose --------------------------------------------------------------


def compute_signal_tape(
    db: Session,
    *,
    history_days: int = DEFAULT_HISTORY_DAYS,
) -> dict[str, Any]:
    """Build the Signal Tape payload — four cells + composite tone.

    Output shape::

        {
          "as_of": "2026-05-10T14:30:00+00:00",
          "history_days": 14,
          "tone": "watching",
          "tone_label": "Watching",
          "cells": [
            {
              "label": "Storms",
              "tier": "watching",
              "tier_label": "Watching",
              "value": "1 active",
              "driver": "TS Foo · 65 kt",
              "history": [{"date": "2026-05-01", "value": 0.0}, ...]
            },
            ...
          ]
        }
    """
    cells = [
        _storms_cell(db, history_days=history_days),
        _equities_cell(db, history_days=history_days),
        _risk_capital_cell(db, history_days=history_days),
        _markets_cell(db, history_days=history_days),
    ]
    tone = _compose_tone(c["tier"] for c in cells)
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "history_days": history_days,
        "tone": tone,
        "tone_label": _TIER_LABEL[tone],
        "cells": cells,
    }


def _compose_tone(tiers: Iterable[Tier]) -> Tier:
    """Composite tone = max tier across all cells."""
    return max(tiers, key=lambda t: _TIER_RANK[t], default="quiet")


# --- Helpers --------------------------------------------------------------


def _latest_quotes_for(db: Session, tickers: list[str]) -> dict[str, TickerQuote]:
    """Latest TickerQuote per ticker, keyed by ticker symbol.

    Same shape as equity_quotes._latest_quote_subquery but inlined here
    so the signal tape doesn't import private helpers from another
    service module.
    """
    sub = (
        select(
            TickerQuote.ticker,
            func.max(TickerQuote.as_of).label("max_ts"),
        )
        .where(TickerQuote.ticker.in_(tickers))
        .group_by(TickerQuote.ticker)
        .subquery()
    )
    rows = db.scalars(
        select(TickerQuote).join(
            sub,
            (TickerQuote.ticker == sub.c.ticker) & (TickerQuote.as_of == sub.c.max_ts),
        ),
    ).all()
    return {row.ticker: row for row in rows}


def _empty_cell(label: str, driver: str) -> dict[str, Any]:
    """Cell payload used when a signal can't be computed (no quotes
    ingested yet, fresh deploy, etc.). Renders as a Quiet-tier neutral
    cell — honest about the absence of data."""
    return {
        "label": label,
        "tier": "quiet",
        "tier_label": _TIER_LABEL["quiet"],
        "value": "—",
        "driver": driver,
        "history": [],
    }


def _date_iso(value: Any) -> str:
    """Coerce a SQL DATE / Python date / datetime into a YYYY-MM-DD
    string. SQLite returns a string, Postgres returns a date — handle
    both."""
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
