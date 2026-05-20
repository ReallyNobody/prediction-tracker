"""Tests for the count-curve service (Day 46).

Lock down the contract the Panel 4 frontend will rely on:

  * Returns the response shape the JS render expects.
  * Parses Kalshi count-series tickers correctly.
  * Computes median via linear interpolation across the 50% crossover.
  * Surfaces monotonicity anomalies as a list rather than silently
    dropping them.
  * Handles an empty DB / off-season gracefully — empty points, null
    median, no exceptions.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from rmn_dashboard.models import PredictionMarket
from rmn_dashboard.services.count_curve import compute_count_curve


def _add_count(
    db: Session,
    *,
    threshold: int,
    yes_price: float,
    season: str = "26",
    last_updated: datetime | None = None,
) -> None:
    base = last_updated or datetime(2026, 5, 13, 14, 0, tzinfo=UTC)
    db.add(
        PredictionMarket(
            platform="kalshi",
            ticker=f"KXHURCTOT-{season}DEC01-T{threshold}",
            title=f"Will there be more than {threshold} Atlantic hurricanes in 20{season}?",
            category="hurricane",
            yes_price=yes_price,
            volume_24h=0.0,
            last_updated=base,
        )
    )


# --- Shape ---------------------------------------------------------------


def test_returns_required_keys(db_session: Session) -> None:
    payload = compute_count_curve(db_session)
    required = {
        "season",
        "season_label",
        "platform",
        "points",
        "median",
        "climate_average",
        "anomalies",
        "as_of",
    }
    assert required.issubset(payload.keys())


def test_platform_is_kalshi(db_session: Session) -> None:
    payload = compute_count_curve(db_session)
    assert payload["platform"] == "kalshi"


def test_climate_average_is_72(db_session: Session) -> None:
    """1991-2020 NOAA Atlantic hurricane climatology. Don't silently
    drift — change requires an editorial decision."""
    payload = compute_count_curve(db_session)
    assert payload["climate_average"] == 7.2


# --- Empty state ---------------------------------------------------------


def test_empty_db_returns_no_points(db_session: Session) -> None:
    payload = compute_count_curve(db_session)
    assert payload["points"] == []
    assert payload["median"] is None
    assert payload["anomalies"] == []
    assert payload["as_of"] is None


# --- Ticker parsing ------------------------------------------------------


def test_parses_thresholds_from_ticker(db_session: Session) -> None:
    _add_count(db_session, threshold=4, yes_price=0.78)
    _add_count(db_session, threshold=10, yes_price=0.07)
    db_session.commit()
    payload = compute_count_curve(db_session)
    thresholds = [p["threshold"] for p in payload["points"]]
    assert thresholds == [4, 10]


def test_ignores_non_count_tickers(db_session: Session) -> None:
    """A landfall-style ticker (KXLANDFL-...) shouldn't appear on the
    count curve even if it shares the hurricane category."""
    _add_count(db_session, threshold=5, yes_price=0.47)
    db_session.add(
        PredictionMarket(
            platform="kalshi",
            ticker="KXLANDFL-26-FL",
            title="Will a hurricane make landfall in FL in 2026?",
            category="hurricane",
            yes_price=0.5,
            last_updated=datetime(2026, 5, 13, 14, 0, tzinfo=UTC),
        )
    )
    db_session.commit()
    payload = compute_count_curve(db_session)
    assert [p["threshold"] for p in payload["points"]] == [5]


def test_handles_only_latest_snapshot_per_ticker(db_session: Session) -> None:
    """Repeated ingest cycles append snapshot rows. The curve should
    use only the latest per ticker."""
    older = datetime(2026, 5, 10, 14, 0, tzinfo=UTC)
    newer = datetime(2026, 5, 13, 14, 0, tzinfo=UTC)
    _add_count(db_session, threshold=4, yes_price=0.85, last_updated=older)
    _add_count(db_session, threshold=4, yes_price=0.78, last_updated=newer)
    db_session.commit()
    payload = compute_count_curve(db_session)
    assert len(payload["points"]) == 1
    # Latest snapshot wins.
    assert payload["points"][0]["yes_price"] == 0.78


# --- Median interpolation -----------------------------------------------


def test_median_interpolation_crosses_50(db_session: Session) -> None:
    """T4=78%, T5=47% → median between 4 and 5, closer to 5."""
    _add_count(db_session, threshold=4, yes_price=0.78)
    _add_count(db_session, threshold=5, yes_price=0.47)
    db_session.commit()
    payload = compute_count_curve(db_session)
    assert payload["median"] is not None
    # (78 - 50) / (78 - 47) = 0.903, so median ≈ 4 + 0.903 = 4.9
    assert 4.85 <= payload["median"] <= 4.95


def test_median_none_when_no_crossover(db_session: Session) -> None:
    """All points above 50% — no 50% crossover exists."""
    _add_count(db_session, threshold=4, yes_price=0.85)
    _add_count(db_session, threshold=5, yes_price=0.75)
    db_session.commit()
    payload = compute_count_curve(db_session)
    assert payload["median"] is None


# --- Anomalies ------------------------------------------------------------


def test_flags_monotonicity_violations(db_session: Session) -> None:
    """P(>8) > P(>7) is mathematically impossible — flag it."""
    _add_count(db_session, threshold=7, yes_price=0.21)
    _add_count(db_session, threshold=8, yes_price=0.22)
    db_session.commit()
    payload = compute_count_curve(db_session)
    assert len(payload["anomalies"]) == 1
    anomaly = payload["anomalies"][0]
    assert anomaly["threshold"] == 8
    assert anomaly["previous_threshold"] == 7


def test_no_anomalies_when_clean(db_session: Session) -> None:
    """A strictly monotonic curve produces zero anomalies."""
    _add_count(db_session, threshold=4, yes_price=0.78)
    _add_count(db_session, threshold=5, yes_price=0.47)
    _add_count(db_session, threshold=6, yes_price=0.29)
    db_session.commit()
    payload = compute_count_curve(db_session)
    assert payload["anomalies"] == []
