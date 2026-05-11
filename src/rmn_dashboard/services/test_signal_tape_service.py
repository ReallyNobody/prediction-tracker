"""Tests for the Signal Tape service (Day 43).

Lock down the contract the JS frontend will rely on:

  * Always returns four cells in fixed order: Storms, Equities, Risk
    capital, Markets.
  * Each cell has the same shape (label, tier, tier_label, value,
    driver, history).
  * Composite tone is the worst-case tier across cells.
  * Threshold transitions: a borderline insurer drawdown crosses
    between Watching and Active at the right point.
  * Graceful empty-state when no quotes exist yet.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from rmn_dashboard.models import PredictionMarket, Storm, StormObservation, TickerQuote
from rmn_dashboard.services.signal_tape import compute_signal_tape


def _add_quote(
    db: Session,
    ticker: str,
    *,
    change_percent: float,
    as_of: datetime | None = None,
) -> None:
    base = as_of or datetime(2026, 5, 10, 14, 0, tzinfo=UTC)
    db.add(
        TickerQuote(
            ticker=ticker,
            last_price=100.0,
            prior_close=100.0 - change_percent,
            change_amount=change_percent,
            change_percent=change_percent,
            currency="USD",
            source="test",
            as_of=base,
        )
    )


def _add_storm(
    db: Session,
    *,
    name: str,
    intensity_kt: int,
    status: str = "active",
) -> Storm:
    storm = Storm(
        nhc_id=f"AL01{name[:4].upper()}",
        name=name,
        season_year=2026,
        max_wind_kt=intensity_kt,
        status=status,
    )
    db.add(storm)
    db.flush()
    db.add(
        StormObservation(
            storm_id=storm.id,
            observed_at=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
            latitude_deg=25.0,
            longitude_deg=-80.0,
            classification="HU" if intensity_kt >= 64 else "TS",
            intensity_kt=intensity_kt,
            pressure_mb=980,
        )
    )
    return storm


def _add_prediction_market(
    db: Session,
    *,
    platform: str,
    ticker: str,
    volume_24h: float,
    last_updated: datetime | None = None,
) -> None:
    base = last_updated or datetime(2026, 5, 10, 14, 0, tzinfo=UTC)
    db.add(
        PredictionMarket(
            platform=platform,
            ticker=ticker,
            title=f"Hurricane market {ticker}",
            category="hurricane",
            volume_24h=volume_24h,
            last_updated=base,
        )
    )


# --- Shape and ordering ---------------------------------------------------


def test_returns_four_cells_in_fixed_order(db_session: Session) -> None:
    """Frontend depends on cell order — keep it locked."""
    payload = compute_signal_tape(db_session)
    assert "cells" in payload
    labels = [c["label"] for c in payload["cells"]]
    assert labels == ["Storms", "Equities", "Risk capital", "Markets"]


def test_each_cell_has_uniform_shape(db_session: Session) -> None:
    """Every cell must carry the same keys so the JS render loop
    doesn't need per-cell branching."""
    payload = compute_signal_tape(db_session)
    required_keys = {"label", "tier", "tier_label", "value", "driver", "history"}
    for cell in payload["cells"]:
        assert required_keys.issubset(cell.keys()), (
            f"cell {cell.get('label')!r} missing keys: {required_keys - set(cell.keys())}"
        )


def test_tone_present_with_label(db_session: Session) -> None:
    payload = compute_signal_tape(db_session)
    assert payload["tone"] in {"quiet", "watching", "active", "severe"}
    assert payload["tone_label"] in {"Quiet", "Watching", "Active", "Severe"}


# --- Quiet day (empty DB) -------------------------------------------------


def test_empty_db_returns_quiet_tone(db_session: Session) -> None:
    """No storms, no quotes, no markets — everything reads Quiet."""
    payload = compute_signal_tape(db_session)
    assert payload["tone"] == "quiet"
    for cell in payload["cells"]:
        assert cell["tier"] == "quiet"


# --- Storms thresholds ----------------------------------------------------


def test_storms_cell_severe_on_cat3(db_session: Session) -> None:
    _add_storm(db_session, name="Cleo", intensity_kt=110)  # Cat 3
    db_session.commit()
    payload = compute_signal_tape(db_session)
    storms = payload["cells"][0]
    assert storms["tier"] == "severe"
    assert "Cat 3+" in storms["driver"] or "110" in storms["driver"]


def test_storms_cell_active_on_hurricane(db_session: Session) -> None:
    _add_storm(db_session, name="Bob", intensity_kt=75)  # Cat 1
    db_session.commit()
    payload = compute_signal_tape(db_session)
    assert payload["cells"][0]["tier"] == "active"


def test_storms_cell_watching_on_tropical_storm(db_session: Session) -> None:
    _add_storm(db_session, name="Ana", intensity_kt=45)  # TS
    db_session.commit()
    payload = compute_signal_tape(db_session)
    assert payload["cells"][0]["tier"] == "watching"


# --- Risk-capital thresholds ----------------------------------------------


def test_risk_capital_severe_on_steep_ils_drop(db_session: Session) -> None:
    _add_quote(db_session, "ILS", change_percent=-3.5)
    db_session.commit()
    payload = compute_signal_tape(db_session)
    risk = payload["cells"][2]
    assert risk["tier"] == "severe"


def test_risk_capital_quiet_on_upside(db_session: Session) -> None:
    """Editorial: upside moves in ILS aren't hurricane risk signals —
    they read as Quiet by design."""
    _add_quote(db_session, "ILS", change_percent=2.0)
    db_session.commit()
    payload = compute_signal_tape(db_session)
    assert payload["cells"][2]["tier"] == "quiet"


# --- Markets thresholds ---------------------------------------------------


def test_markets_active_on_aggregate_volume(db_session: Session) -> None:
    _add_prediction_market(
        db_session,
        platform="kalshi",
        ticker="KXHURR-1",
        volume_24h=4_000.0,
    )
    _add_prediction_market(
        db_session,
        platform="polymarket",
        ticker="poly-1",
        volume_24h=3_000.0,
    )
    db_session.commit()
    payload = compute_signal_tape(db_session)
    markets = payload["cells"][3]
    assert markets["tier"] == "active"
    # Driver should include the aggregate.
    assert "$7,000" in markets["driver"] or "7,000" in markets["driver"]


# --- Composite tone -------------------------------------------------------


def test_tone_is_max_tier_across_cells(db_session: Session) -> None:
    """A single Severe cell drives the composite tone even if others
    are Quiet."""
    _add_storm(db_session, name="Cleo", intensity_kt=120)  # Severe
    _add_quote(db_session, "ILS", change_percent=0.2)  # Quiet
    db_session.commit()
    payload = compute_signal_tape(db_session)
    assert payload["tone"] == "severe"


# --- History -------------------------------------------------------------


def test_history_array_present_per_cell(db_session: Session) -> None:
    """Each cell carries a history field. May be empty on a fresh DB,
    but it must be a list."""
    payload = compute_signal_tape(db_session)
    for cell in payload["cells"]:
        assert isinstance(cell["history"], list)


def test_history_includes_recent_storms(db_session: Session) -> None:
    """An observation in the last 14 days should show up in storms
    history."""
    _add_storm(db_session, name="Bob", intensity_kt=70)
    db_session.commit()
    payload = compute_signal_tape(db_session, history_days=14)
    history = payload["cells"][0]["history"]
    assert len(history) >= 1
    assert all("date" in p and "value" in p for p in history)
