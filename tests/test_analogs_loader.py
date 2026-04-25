"""Tests for the historical-analogs loader (``data/analogs.py``).

Mirrors the structure of ``test_universe.py``. Lock down:

  * The shipped YAML round-trips through validation cleanly.
  * Every analog has a US state code, a sensible peak intensity, and
    a Western-Hemisphere lat/lon (so a sign-flipped longitude can't
    sneak in pointing at India).
  * Duplicate (name, year) pairs are rejected.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from rmn_dashboard.data.analogs import HistoricalAnalogs, load_analogs


@pytest.fixture(autouse=True)
def _clear_loader_cache() -> None:
    load_analogs.cache_clear()


def _write_yaml(tmp_path: Path, body: str) -> Path:
    target = tmp_path / "analogs.yaml"
    target.write_text(dedent(body), encoding="utf-8")
    return target


_BASE_HEADER = """\
version: 1
last_reviewed: 2026-04-25
analogs:
"""


# ----- Bundled YAML smoke tests ------------------------------------------


def test_bundled_analogs_load_cleanly() -> None:
    analogs_doc = load_analogs()
    assert isinstance(analogs_doc, HistoricalAnalogs)
    # 12 in the launch roster; allow growth without pinning the count.
    assert 8 <= len(analogs_doc.analogs) <= 30
    # Every shipped analog has a non-trivial narrative.
    assert all(len(a.narrative) > 20 for a in analogs_doc.analogs)


def test_bundled_analogs_have_no_duplicates() -> None:
    analogs_doc = load_analogs()
    keys = [(a.name, a.year) for a in analogs_doc.analogs]
    assert len(keys) == len(set(keys))


def test_bundled_analogs_landfall_lons_are_western_hemisphere() -> None:
    """A sign-flipped longitude (positive instead of negative) would
    place a US analog over Asia. The model rejects it; this test is
    explicit so a regression that loosens the validator fails loud.
    """
    analogs_doc = load_analogs()
    for a in analogs_doc.analogs:
        assert -100.0 <= a.landfall_lon <= -60.0, (
            f"{a.name} {a.year} has lon {a.landfall_lon} outside Western Hemisphere"
        )


# ----- Validator negative cases ------------------------------------------


def test_load_rejects_unknown_state(tmp_path: Path) -> None:
    body = _BASE_HEADER + dedent(
        """\
          - name: Hurricane Test
            year: 2020
            peak_kt: 100
            saffir_simpson_at_landfall: 3
            landfall_lat: 27.0
            landfall_lon: -82.0
            landfall_state: XX
            insured_loss_usd_billions: 1.0
            narrative: Test storm.
        """
    )
    target = _write_yaml(tmp_path, body)
    with pytest.raises(ValueError, match="not a recognized US state code"):
        load_analogs(target)


def test_load_rejects_eastern_hemisphere_longitude(tmp_path: Path) -> None:
    """Sign-flip protection: positive longitudes (e.g., +82.0) point
    at Asia, not the US. Reject loudly.
    """
    body = _BASE_HEADER + dedent(
        """\
          - name: Hurricane Test
            year: 2020
            peak_kt: 100
            saffir_simpson_at_landfall: 3
            landfall_lat: 27.0
            landfall_lon: 82.0
            landfall_state: FL
            insured_loss_usd_billions: 1.0
            narrative: Test storm.
        """
    )
    target = _write_yaml(tmp_path, body)
    with pytest.raises(ValueError):
        load_analogs(target)


def test_load_rejects_duplicate_name_year_pair(tmp_path: Path) -> None:
    body = _BASE_HEADER + dedent(
        """\
          - name: Hurricane Foo
            year: 2020
            peak_kt: 100
            saffir_simpson_at_landfall: 3
            landfall_lat: 27.0
            landfall_lon: -82.0
            landfall_state: FL
            insured_loss_usd_billions: 1.0
            narrative: First.
          - name: Hurricane Foo
            year: 2020
            peak_kt: 110
            saffir_simpson_at_landfall: 4
            landfall_lat: 28.0
            landfall_lon: -82.0
            landfall_state: FL
            insured_loss_usd_billions: 1.0
            narrative: Duplicate.
        """
    )
    target = _write_yaml(tmp_path, body)
    with pytest.raises(ValueError, match="duplicate analog entry"):
        load_analogs(target)


def test_load_rejects_invalid_saffir_simpson(tmp_path: Path) -> None:
    """Cat 6 doesn't exist (yet). Cat 0 is meaningless. Reject."""
    body = _BASE_HEADER + dedent(
        """\
          - name: Hurricane Test
            year: 2020
            peak_kt: 100
            saffir_simpson_at_landfall: 6
            landfall_lat: 27.0
            landfall_lon: -82.0
            landfall_state: FL
            insured_loss_usd_billions: 1.0
            narrative: Test storm.
        """
    )
    target = _write_yaml(tmp_path, body)
    with pytest.raises(ValueError):
        load_analogs(target)
