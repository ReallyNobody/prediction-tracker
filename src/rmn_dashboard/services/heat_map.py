"""Prediction-market heat-map service — joins canonical questions to
``prediction_markets`` snapshots and computes day-over-day price deltas.

Powers Panel 8 (Modeled market moves). The data layer in
``data/heat_map.py`` defines the editorial canon — which questions
exist, which platform carries each. This module is the read-side: it
takes that question registry, queries the latest snapshot per (platform,
ticker) plus a reference snapshot from ~24h prior, and emits a flat
list of cells the JS renderer maps into a 2D grid.

Day-over-day delta semantics (chosen deliberately):

  * "Today" = the latest snapshot for a (platform, ticker), full stop.
  * "Yesterday" = the latest snapshot whose ``last_updated`` is at least
    23 hours before today's. The 23h floor (rather than exactly 24h)
    accommodates scrape-time jitter — if the cron lands at 04:03 UTC
    today and 04:11 UTC yesterday, we still pair them correctly. If no
    snapshot >= 23h old exists, the cell's delta is ``null`` rather
    than fabricated.
  * Delta is in cents (yes-price points). Polymarket and Kalshi are
    both normalized to 0-100 cent yes-prices upstream, so the delta is
    apples-to-apples without further scaling.

Why a flat cell list rather than a 2D matrix:

  * Extensible. Adding cell-level metadata (URLs, market-status flags,
    settled markets) doesn't require versioning the grid shape.
  * Symmetric with the Panel 7 / Panel 5 service shapes — the JS
    renderer always iterates an array, never indexes a matrix.
  * Empty cells (platform doesn't carry the question) still appear so
    the renderer's grid stays uniform; ``has_data`` and
    ``missing_reason`` say why.

Quietness signal:

  * ``is_quiet`` is true when the average absolute delta across cells
    with data is below ``_QUIETNESS_THRESHOLD_CENTS``. The UI uses this
    to swap in the "Markets are quiet — no significant moves in 24h"
    caption (per Panel 8 editorial decision, option b: ship the panel
    always, caption when low signal). Threshold is editorial — 1.0
    cents matches a ~1pp probability move, the minimum reader-relevant
    threshold for "actually something happened today" framing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rmn_dashboard.data.heat_map import (
    CanonicalQuestion,
    HeatMapQuestions,
    load_heat_map_questions,
)
from rmn_dashboard.models import PredictionMarket

# Pair-snapshot cushion. The cron ingestor doesn't run at exactly the
# same wall-clock second every day, so demanding "exactly 24h apart"
# would miss valid yesterday-snapshots by a few minutes. 23h is the
# floor — still clearly "yesterday" editorially, robust to ~1h of cron
# jitter.
_YESTERDAY_MIN_DELTA = timedelta(hours=23)

# Outer window for the "yesterday" search. We don't want a snapshot
# from a week ago masquerading as yesterday's price if the scraper was
# down — that would distort the delta. 36h leaves room for one missed
# scrape interval but cuts off stale data.
_YESTERDAY_MAX_DELTA = timedelta(hours=36)

# Average |delta| across cells below this threshold flips the panel to
# its "quiet" caption. 1.0 cents = 1 percentage point on yes-price, the
# minimum reader-relevant move.
_QUIETNESS_THRESHOLD_CENTS = 1.0

_FRAMING = "Day-over-day price moves across prediction markets"


def heat_map_payload(
    db: Session,
    *,
    questions: HeatMapQuestions | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the panel-ready heat-map payload.

    ``questions`` and ``now`` are injectable for tests — production
    callers pass neither and get the bundled YAML + wall-clock time.

    Response shape::

        {
          "as_of":     "2026-06-15T14:30:00+00:00",
          "framing":   "Day-over-day price moves...",
          "platforms": ["kalshi", "polymarket"],
          "questions": [
            {"id": "...", "short_label": "...",
             "long_label": "...", "category": "..."},
            ...
          ],
          "cells": [
            {"platform": "kalshi", "question_id": "atlantic-count-ge-5",
             "ticker": "KXHURCTOT-26DEC01-T5",
             "yes_price": 78.0,
             "delta_24h": -3.5,
             "volume_24h": 1234.0,
             "has_data": true,
             "missing_reason": null},
            ...
          ],
          "is_quiet": false
        }
    """
    doc = questions if questions is not None else load_heat_map_questions()
    as_of = now if now is not None else datetime.now(UTC)

    platforms = list(doc.platforms_present())
    question_dicts = [
        {
            "id": q.id,
            "short_label": q.short_label,
            "long_label": q.long_label,
            "category": q.category,
        }
        for q in doc.questions
    ]

    cells: list[dict[str, Any]] = []
    for platform in platforms:
        for question in doc.questions:
            cells.append(_build_cell(db, platform, question, as_of=as_of))

    return {
        "as_of": as_of.isoformat(),
        "framing": _FRAMING,
        "platforms": platforms,
        "questions": question_dicts,
        "cells": cells,
        "is_quiet": _is_quiet(cells),
    }


def _build_cell(
    db: Session,
    platform: str,
    question: CanonicalQuestion,
    *,
    as_of: datetime,
) -> dict[str, Any]:
    """Build one heat-map cell — the intersection of one platform row
    and one question column."""
    ticker = question.link_for(platform)

    base: dict[str, Any] = {
        "platform": platform,
        "question_id": question.id,
        "ticker": ticker,
        "yes_price": None,
        "delta_24h": None,
        "volume_24h": None,
        "has_data": False,
        "missing_reason": None,
    }

    if ticker is None:
        # Editorial assertion: this platform doesn't carry this
        # question. Renders as an empty cell with the corresponding
        # tooltip — not a bug, an absence of coverage.
        base["missing_reason"] = "platform_does_not_carry"
        return base

    today = _latest_snapshot(db, platform=platform, ticker=ticker, as_of=as_of)
    if today is None:
        # Editorial assertion failed at runtime: the ticker is in the
        # YAML but no snapshot exists. Either a typo'd ticker or a
        # scraper that hasn't seen this market yet. Editorial gets
        # signaled via the missing_reason; the heat-map shows an empty
        # cell.
        base["missing_reason"] = "no_recent_snapshot"
        return base

    yesterday = _yesterday_snapshot(
        db, platform=platform, ticker=ticker, today_ts=today.last_updated
    )

    base["yes_price"] = today.yes_price
    base["volume_24h"] = today.volume_24h
    base["has_data"] = today.yes_price is not None
    if yesterday is not None and yesterday.yes_price is not None and today.yes_price is not None:
        base["delta_24h"] = today.yes_price - yesterday.yes_price
    return base


def _latest_snapshot(
    db: Session,
    *,
    platform: str,
    ticker: str,
    as_of: datetime,
) -> PredictionMarket | None:
    """Most recent snapshot row for the given (platform, ticker).

    ``as_of`` is the upper bound — production passes wall-clock time,
    tests pass a frozen instant. Filtering by ``<= as_of`` means a test
    can seed a DB with snapshots from arbitrary timestamps and assert
    the right one wins.
    """
    stmt = (
        select(PredictionMarket)
        .where(
            PredictionMarket.platform == platform,
            PredictionMarket.ticker == ticker,
            PredictionMarket.last_updated <= as_of,
        )
        .order_by(PredictionMarket.last_updated.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def _yesterday_snapshot(
    db: Session,
    *,
    platform: str,
    ticker: str,
    today_ts: datetime,
) -> PredictionMarket | None:
    """Most recent snapshot row for (platform, ticker) whose
    ``last_updated`` is at least 23h (and at most 36h) before
    ``today_ts``.

    The 23h floor handles scrape-time jitter; the 36h ceiling prevents
    a week-old snapshot from masquerading as "yesterday" if the
    scraper was down. If no snapshot satisfies both, returns None and
    the cell's delta becomes null."""
    lower_bound = today_ts - _YESTERDAY_MAX_DELTA
    upper_bound = today_ts - _YESTERDAY_MIN_DELTA
    stmt = (
        select(PredictionMarket)
        .where(
            PredictionMarket.platform == platform,
            PredictionMarket.ticker == ticker,
            PredictionMarket.last_updated >= lower_bound,
            PredictionMarket.last_updated <= upper_bound,
        )
        .order_by(PredictionMarket.last_updated.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def _is_quiet(cells: list[dict[str, Any]]) -> bool:
    """Average absolute delta across cells with data below threshold →
    panel renders its 'markets are quiet' caption."""
    deltas = [abs(c["delta_24h"]) for c in cells if c["delta_24h"] is not None]
    if not deltas:
        # No comparisons available — editorially treat as quiet so the
        # panel renders the soft caption rather than implying activity
        # we can't confirm.
        return True
    avg_abs_delta = sum(deltas) / len(deltas)
    return avg_abs_delta < _QUIETNESS_THRESHOLD_CENTS
