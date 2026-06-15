"""Tests for the cat-loss-estimates loader (``data/cat_losses.py``).

Mirrors the structure of ``test_analogs_loader.py``. Lock down:

  * The shipped YAML round-trips through validation cleanly.
  * Every estimate has a known modeler, a sane range (low <= high),
    and a non-future issuance date.
  * Duplicate (modeler, issued_at) pairs inside one event are rejected
    — same modeler can refine on different days but two rows on the
    same day is almost certainly a curation typo.
  * Duplicate (event_name, year) pairs at the top level are rejected.
  * The derived helpers (``latest_per_modeler``,
    ``consensus_midpoint_usd_billions``, ``dispersion_usd_billions``,
    ``midpoint_usd_billions``, ``is_point_estimate``) return what the
    panel layer will expect.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from textwrap import dedent

import pytest

from rmn_dashboard.data.cat_losses import (
    CatLossEstimates,
    CatLossEvent,
    load_cat_losses,
)


@pytest.fixture(autouse=True)
def _clear_loader_cache() -> None:
    load_cat_losses.cache_clear()


def _write_yaml(tmp_path: Path, body: str) -> Path:
    target = tmp_path / "cat_loss_estimates.yaml"
    target.write_text(dedent(body), encoding="utf-8")
    return target


_BASE_HEADER = """\
version: 1
last_reviewed: 2026-06-12
events:
"""


# ----- Bundled YAML smoke tests ------------------------------------------


def test_bundled_cat_losses_load_cleanly() -> None:
    losses = load_cat_losses()
    assert isinstance(losses, CatLossEstimates)
    # Three at launch (Ian, Helene, Milton); allow growth without pinning
    # the count.
    assert 3 <= len(losses.events) <= 30
    # Every event must have at least one estimate.
    assert all(len(e.estimates) >= 1 for e in losses.events)


def test_bundled_cat_losses_have_no_duplicate_events() -> None:
    losses = load_cat_losses()
    keys = [(e.event_name, e.year) for e in losses.events]
    assert len(keys) == len(set(keys))


def test_bundled_cat_losses_have_no_future_issuances() -> None:
    """A future issued_at usually means a YYYY/DD/MM vs YYYY/MM/DD
    typo. Catch it before it ships."""
    losses = load_cat_losses()
    today = date.today()
    for event in losses.events:
        for est in event.estimates:
            assert est.issued_at <= today, (
                f"{event.event_name} {event.year} / {est.modeler}: "
                f"issued_at {est.issued_at} is in the future"
            )


def test_bundled_cat_losses_have_low_le_high() -> None:
    """Belt-and-suspenders on the Pydantic validator."""
    losses = load_cat_losses()
    for event in losses.events:
        for est in event.estimates:
            assert est.low_usd_billions <= est.high_usd_billions


# ----- Schema enforcement -------------------------------------------------


def test_unknown_modeler_rejected(tmp_path: Path) -> None:
    yaml_path = _write_yaml(
        tmp_path,
        _BASE_HEADER
        + """\
          - event_name: Hurricane Test
            year: 2024
            estimates:
              - modeler: Made Up Co.
                low_usd_billions: 1.0
                high_usd_billions: 2.0
                issued_at: 2024-10-01
        """,
    )
    with pytest.raises(ValueError, match="not in the known set"):
        load_cat_losses(yaml_path)


def test_low_greater_than_high_rejected(tmp_path: Path) -> None:
    yaml_path = _write_yaml(
        tmp_path,
        _BASE_HEADER
        + """\
          - event_name: Hurricane Test
            year: 2024
            estimates:
              - modeler: Verisk
                low_usd_billions: 20.0
                high_usd_billions: 10.0
                issued_at: 2024-10-01
        """,
    )
    with pytest.raises(ValueError, match="must be <="):
        load_cat_losses(yaml_path)


def test_duplicate_issuance_inside_event_rejected(tmp_path: Path) -> None:
    yaml_path = _write_yaml(
        tmp_path,
        _BASE_HEADER
        + """\
          - event_name: Hurricane Test
            year: 2024
            estimates:
              - modeler: Verisk
                low_usd_billions: 10.0
                high_usd_billions: 15.0
                issued_at: 2024-10-01
              - modeler: Verisk
                low_usd_billions: 11.0
                high_usd_billions: 16.0
                issued_at: 2024-10-01
        """,
    )
    with pytest.raises(ValueError, match="duplicate estimate"):
        load_cat_losses(yaml_path)


def test_duplicate_event_at_top_level_rejected(tmp_path: Path) -> None:
    yaml_path = _write_yaml(
        tmp_path,
        _BASE_HEADER
        + """\
          - event_name: Hurricane Test
            year: 2024
            estimates:
              - modeler: Verisk
                low_usd_billions: 10.0
                high_usd_billions: 15.0
                issued_at: 2024-10-01
          - event_name: Hurricane Test
            year: 2024
            estimates:
              - modeler: Moody's RMS
                low_usd_billions: 12.0
                high_usd_billions: 18.0
                issued_at: 2024-10-02
        """,
    )
    with pytest.raises(ValueError, match="duplicate event"):
        load_cat_losses(yaml_path)


def test_event_with_no_estimates_rejected(tmp_path: Path) -> None:
    yaml_path = _write_yaml(
        tmp_path,
        _BASE_HEADER
        + """\
          - event_name: Hurricane Test
            year: 2024
            estimates: []
        """,
    )
    with pytest.raises(ValueError, match="at least one estimate"):
        load_cat_losses(yaml_path)


# ----- Derived helpers ----------------------------------------------------


def _build_event() -> CatLossEvent:
    """Synthetic event with two modelers across three issuance dates —
    enough to exercise latest_per_modeler() and the consensus helpers."""
    return CatLossEvent.model_validate(
        {
            "event_name": "Hurricane Test",
            "year": 2024,
            "estimates": [
                # Verisk: two refinements, latest should win
                {
                    "modeler": "Verisk",
                    "low_usd_billions": 10.0,
                    "high_usd_billions": 20.0,
                    "issued_at": date(2024, 10, 1),
                },
                {
                    "modeler": "Verisk",
                    "low_usd_billions": 12.0,
                    "high_usd_billions": 16.0,
                    "issued_at": date(2024, 10, 15),
                },
                # KCC: point estimate (low == high)
                {
                    "modeler": "Karen Clark & Company",
                    "low_usd_billions": 18.0,
                    "high_usd_billions": 18.0,
                    "issued_at": date(2024, 10, 2),
                },
            ],
        }
    )


def test_latest_per_modeler_returns_one_per_firm() -> None:
    event = _build_event()
    latest = event.latest_per_modeler()
    assert len(latest) == 2
    assert {e.modeler for e in latest} == {"Verisk", "Karen Clark & Company"}
    # Verisk's later refinement wins:
    verisk = next(e for e in latest if e.modeler == "Verisk")
    assert verisk.issued_at == date(2024, 10, 15)
    assert verisk.midpoint_usd_billions == 14.0


def test_consensus_midpoint_averages_latest_per_modeler() -> None:
    event = _build_event()
    # Verisk latest midpoint = 14.0; KCC = 18.0; average = 16.0
    assert event.consensus_midpoint_usd_billions == 16.0


def test_dispersion_is_max_minus_min_of_latest_midpoints() -> None:
    event = _build_event()
    # |18.0 - 14.0| = 4.0
    assert event.dispersion_usd_billions == 4.0


def test_dispersion_is_zero_with_single_modeler() -> None:
    event = CatLossEvent.model_validate(
        {
            "event_name": "Hurricane Solo",
            "year": 2024,
            "estimates": [
                {
                    "modeler": "Verisk",
                    "low_usd_billions": 10.0,
                    "high_usd_billions": 20.0,
                    "issued_at": date(2024, 10, 1),
                }
            ],
        }
    )
    assert event.dispersion_usd_billions == 0.0


def test_point_estimate_flag() -> None:
    event = _build_event()
    latest = event.latest_per_modeler()
    kcc = next(e for e in latest if e.modeler == "Karen Clark & Company")
    verisk = next(e for e in latest if e.modeler == "Verisk")
    assert kcc.is_point_estimate is True
    assert verisk.is_point_estimate is False
