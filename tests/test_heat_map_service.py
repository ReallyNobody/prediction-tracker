"""Tests for the heat-map service (``services/heat_map.py``).

Lock down: grid shape (one cell per platform × question pair), the
day-over-day delta semantics (23-36h pairing window), the empty-cell
and missing-snapshot cases, and the ``is_quiet`` editorial signal.

DB seeding strategy: each test inserts ``PredictionMarket`` rows at
controlled timestamps and then calls ``heat_map_payload`` with an
injected ``now`` so timing assertions don't depend on wall-clock time.
The canonical-question registry is also injectable so test cases can
construct minimal one-row docs without touching the bundled YAML.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from rmn_dashboard.data.heat_map import HeatMapQuestions
from rmn_dashboard.models import PredictionMarket
from rmn_dashboard.services.heat_map import heat_map_payload

# ---- Builders ------------------------------------------------------------


def _doc(questions: list[dict[str, Any]]) -> HeatMapQuestions:
    """Build an in-memory HeatMapQuestions doc, bypassing the YAML
    loader so tests can construct edge cases concisely."""
    return HeatMapQuestions.model_validate(
        {
            "version": 1,
            "last_reviewed": "2026-06-15",
            "questions": questions,
        }
    )


def _q_both() -> dict[str, Any]:
    """Canonical question carried on both platforms."""
    return {
        "id": "atlantic-count-ge-5",
        "short_label": "Count ≥5",
        "long_label": "Atlantic named storms ≥5 for 2026",
        "category": "count",
        "platforms": {
            "kalshi": "KX-T5",
            "polymarket": "poly-count-ge-5",
        },
    }


def _q_kalshi_only() -> dict[str, Any]:
    """Canonical question only on Kalshi."""
    return {
        "id": "atlantic-count-ge-7",
        "short_label": "Count ≥7",
        "long_label": "Atlantic named storms ≥7 for 2026",
        "category": "count",
        "platforms": {"kalshi": "KX-T7"},
    }


def _snapshot(
    db: Session,
    *,
    platform: str,
    ticker: str,
    yes_price: float | None,
    when: datetime,
    volume_24h: float | None = 1000.0,
) -> None:
    """Insert one PredictionMarket snapshot row."""
    row = PredictionMarket(
        platform=platform,
        ticker=ticker,
        title=f"{ticker} title",
        category="hurricane",
        yes_price=yes_price,
        no_price=(100.0 - yes_price) if yes_price is not None else None,
        volume_24h=volume_24h,
        last_updated=when,
    )
    db.add(row)
    db.commit()


# ---- Grid shape ----------------------------------------------------------


def test_grid_has_one_cell_per_platform_question_pair(db_session: Session) -> None:
    doc = _doc([_q_both(), _q_kalshi_only()])
    payload = heat_map_payload(db_session, questions=doc, now=datetime(2026, 6, 15, tzinfo=UTC))
    # 2 platforms × 2 questions = 4 cells
    assert len(payload["cells"]) == 4
    pairs = {(c["platform"], c["question_id"]) for c in payload["cells"]}
    assert pairs == {
        ("kalshi", "atlantic-count-ge-5"),
        ("kalshi", "atlantic-count-ge-7"),
        ("polymarket", "atlantic-count-ge-5"),
        ("polymarket", "atlantic-count-ge-7"),  # platform-does-not-carry cell
    }


def test_platforms_present_orders_kalshi_first(db_session: Session) -> None:
    doc = _doc([_q_both()])
    payload = heat_map_payload(db_session, questions=doc, now=datetime(2026, 6, 15, tzinfo=UTC))
    assert payload["platforms"][0] == "kalshi"


def test_questions_include_metadata(db_session: Session) -> None:
    """The JS renderer needs the column headers + tooltips out of the
    payload — no second round trip to the schema."""
    doc = _doc([_q_both()])
    payload = heat_map_payload(db_session, questions=doc, now=datetime(2026, 6, 15, tzinfo=UTC))
    assert len(payload["questions"]) == 1
    q = payload["questions"][0]
    assert set(q.keys()) == {"id", "short_label", "long_label", "category"}
    assert q["short_label"] == "Count ≥5"
    assert q["category"] == "count"


# ---- Day-over-day delta semantics ----------------------------------------


def test_cell_with_two_snapshots_24h_apart_has_delta(db_session: Session) -> None:
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    _snapshot(
        db_session,
        platform="kalshi",
        ticker="KX-T5",
        yes_price=72.0,
        when=now - timedelta(hours=24),
    )
    _snapshot(
        db_session,
        platform="kalshi",
        ticker="KX-T5",
        yes_price=78.0,
        when=now,
    )
    payload = heat_map_payload(db_session, questions=_doc([_q_both()]), now=now)
    cell = next(c for c in payload["cells"] if c["platform"] == "kalshi")
    assert cell["yes_price"] == 78.0
    assert cell["delta_24h"] == 6.0
    assert cell["has_data"] is True


def test_yesterday_snapshot_inside_23h_window_is_rejected(db_session: Session) -> None:
    """A snapshot 20h ago is too fresh — using it as 'yesterday' would
    understate the daily move. Service must skip it and return null delta."""
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    _snapshot(
        db_session,
        platform="kalshi",
        ticker="KX-T5",
        yes_price=72.0,
        when=now - timedelta(hours=20),  # too fresh
    )
    _snapshot(db_session, platform="kalshi", ticker="KX-T5", yes_price=78.0, when=now)
    payload = heat_map_payload(db_session, questions=_doc([_q_both()]), now=now)
    cell = next(c for c in payload["cells"] if c["platform"] == "kalshi")
    assert cell["yes_price"] == 78.0
    assert cell["delta_24h"] is None  # no valid pair


def test_yesterday_snapshot_older_than_36h_is_rejected(db_session: Session) -> None:
    """A snapshot 48h ago is too stale — would silently treat a two-day
    move as a one-day move. Service must skip it."""
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    _snapshot(
        db_session,
        platform="kalshi",
        ticker="KX-T5",
        yes_price=60.0,
        when=now - timedelta(hours=48),  # too stale
    )
    _snapshot(db_session, platform="kalshi", ticker="KX-T5", yes_price=78.0, when=now)
    payload = heat_map_payload(db_session, questions=_doc([_q_both()]), now=now)
    cell = next(c for c in payload["cells"] if c["platform"] == "kalshi")
    assert cell["delta_24h"] is None


def test_yesterday_picks_latest_in_window_when_multiple_match(db_session: Session) -> None:
    """If 3 snapshots fall inside the 23-36h window, the latest within
    that window wins — closest to '24h ago' wins editorially."""
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    _snapshot(
        db_session,
        platform="kalshi",
        ticker="KX-T5",
        yes_price=70.0,
        when=now - timedelta(hours=35),
    )
    _snapshot(
        db_session,
        platform="kalshi",
        ticker="KX-T5",
        yes_price=73.0,
        when=now - timedelta(hours=24),  # latest in window
    )
    _snapshot(db_session, platform="kalshi", ticker="KX-T5", yes_price=78.0, when=now)
    payload = heat_map_payload(db_session, questions=_doc([_q_both()]), now=now)
    cell = next(c for c in payload["cells"] if c["platform"] == "kalshi")
    # Delta from 73 (the closer yesterday) → 78, not 70 → 78.
    assert cell["delta_24h"] == 5.0


# ---- Empty / missing cases -----------------------------------------------


def test_platform_does_not_carry_cell_has_missing_reason(db_session: Session) -> None:
    """A canonical question that's Kalshi-only must still have a
    Polymarket cell — flagged so the grid renders uniformly."""
    doc = _doc([_q_kalshi_only(), _q_both()])  # need _q_both to populate polymarket row
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    payload = heat_map_payload(db_session, questions=doc, now=now)
    poly_only_cell = next(
        c
        for c in payload["cells"]
        if c["platform"] == "polymarket" and c["question_id"] == "atlantic-count-ge-7"
    )
    assert poly_only_cell["has_data"] is False
    assert poly_only_cell["missing_reason"] == "platform_does_not_carry"
    assert poly_only_cell["ticker"] is None


def test_ticker_with_no_snapshot_flagged_missing(db_session: Session) -> None:
    """Editorial says Kalshi carries KX-T5, but no scrape has produced
    rows yet — cell shows 'no_recent_snapshot' rather than silently
    rendering as if the platform doesn't carry it."""
    doc = _doc([_q_both()])
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    payload = heat_map_payload(db_session, questions=doc, now=now)
    cell = next(c for c in payload["cells"] if c["platform"] == "kalshi")
    assert cell["has_data"] is False
    assert cell["missing_reason"] == "no_recent_snapshot"
    assert cell["ticker"] == "KX-T5"  # the editorial assertion is still surfaced


def test_single_snapshot_renders_price_without_delta(db_session: Session) -> None:
    """Fresh deploy: only one snapshot exists. Cell shows the price but
    no delta — better than hiding the cell."""
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    _snapshot(db_session, platform="kalshi", ticker="KX-T5", yes_price=78.0, when=now)
    payload = heat_map_payload(db_session, questions=_doc([_q_both()]), now=now)
    cell = next(c for c in payload["cells"] if c["platform"] == "kalshi")
    assert cell["yes_price"] == 78.0
    assert cell["delta_24h"] is None
    assert cell["has_data"] is True
    assert cell["missing_reason"] is None


# ---- is_quiet signal -----------------------------------------------------


def test_is_quiet_true_when_no_deltas(db_session: Session) -> None:
    """No comparisons available → treat as quiet so the caption shows
    rather than implying activity we can't confirm."""
    payload = heat_map_payload(
        db_session, questions=_doc([_q_both()]), now=datetime(2026, 6, 15, tzinfo=UTC)
    )
    assert payload["is_quiet"] is True


def test_is_quiet_true_when_avg_delta_under_threshold(db_session: Session) -> None:
    """Small moves on a single market still count as 'quiet'."""
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    _snapshot(
        db_session,
        platform="kalshi",
        ticker="KX-T5",
        yes_price=78.0,
        when=now - timedelta(hours=24),
    )
    _snapshot(db_session, platform="kalshi", ticker="KX-T5", yes_price=78.3, when=now)
    payload = heat_map_payload(db_session, questions=_doc([_q_both()]), now=now)
    assert payload["is_quiet"] is True  # 0.3 cents avg < 1.0


def test_is_quiet_false_when_avg_delta_over_threshold(db_session: Session) -> None:
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    _snapshot(
        db_session,
        platform="kalshi",
        ticker="KX-T5",
        yes_price=70.0,
        when=now - timedelta(hours=24),
    )
    _snapshot(db_session, platform="kalshi", ticker="KX-T5", yes_price=78.0, when=now)
    payload = heat_map_payload(db_session, questions=_doc([_q_both()]), now=now)
    assert payload["is_quiet"] is False  # 8 cents > 1.0


# ---- Top-level shape ----------------------------------------------------


def test_payload_keys_lock_down(db_session: Session) -> None:
    payload = heat_map_payload(
        db_session, questions=_doc([_q_both()]), now=datetime(2026, 6, 15, tzinfo=UTC)
    )
    assert set(payload.keys()) == {
        "as_of",
        "framing",
        "platforms",
        "questions",
        "cells",
        "is_quiet",
    }


def test_bundled_yaml_renders_through_service(db_session: Session) -> None:
    """End-to-end smoke: the bundled YAML loads and produces a valid
    payload. Catches data/service contract drift."""
    payload = heat_map_payload(db_session)
    assert "kalshi" in payload["platforms"]
    assert "polymarket" in payload["platforms"]
    assert len(payload["questions"]) >= 3
    # Cells = platforms × questions, no holes.
    assert len(payload["cells"]) == len(payload["platforms"]) * len(payload["questions"])
