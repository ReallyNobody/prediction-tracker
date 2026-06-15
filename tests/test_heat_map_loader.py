"""Tests for the heat-map question registry (``data/heat_map.py``).

Mirrors the structure of ``test_cat_losses_loader.py``. Lock down:

  * The shipped YAML round-trips through validation cleanly.
  * Every question references known platforms and known categories.
  * Duplicate question IDs are rejected.
  * Empty platform maps and empty ticker strings are rejected — both
    are silent-failure shapes (a question with no platforms renders no
    data; a question with an empty ticker silently returns no DB row).
  * Slug-shaped IDs are enforced so JSON keys and URL fragments stay
    predictable.
  * Derived helpers (``link_for``, ``platforms_present``,
    ``questions_for_platform``) return what the service layer will need.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from rmn_dashboard.data.heat_map import (
    _KNOWN_CATEGORIES,
    _KNOWN_PLATFORMS,
    HeatMapQuestions,
    load_heat_map_questions,
)


@pytest.fixture(autouse=True)
def _clear_loader_cache() -> None:
    load_heat_map_questions.cache_clear()


def _write_yaml(tmp_path: Path, body: str) -> Path:
    target = tmp_path / "prediction_market_questions.yaml"
    target.write_text(dedent(body), encoding="utf-8")
    return target


_BASE_HEADER = """\
version: 1
last_reviewed: 2026-06-15
questions:
"""


# ----- Bundled YAML smoke tests ------------------------------------------


def test_bundled_heat_map_loads_cleanly() -> None:
    doc = load_heat_map_questions()
    assert isinstance(doc, HeatMapQuestions)
    # Three at launch (count-ge-5 both-platform, count-ge-7 Kalshi-only,
    # first-hurricane-by-aug-1 Polymarket-only). Allow editorial growth
    # without pinning the count.
    assert 3 <= len(doc.questions) <= 30


def test_bundled_heat_map_has_no_duplicate_ids() -> None:
    doc = load_heat_map_questions()
    ids = [q.id for q in doc.questions]
    assert len(ids) == len(set(ids))


def test_bundled_heat_map_categories_are_known() -> None:
    doc = load_heat_map_questions()
    for q in doc.questions:
        assert q.category in _KNOWN_CATEGORIES


def test_bundled_heat_map_platforms_are_known() -> None:
    doc = load_heat_map_questions()
    for q in doc.questions:
        for platform in q.platforms:
            assert platform in _KNOWN_PLATFORMS


# ----- Schema enforcement -------------------------------------------------


def test_unknown_platform_rejected(tmp_path: Path) -> None:
    yaml_path = _write_yaml(
        tmp_path,
        _BASE_HEADER
        + """\
          - id: bad-platform
            short_label: Bad
            long_label: Some long description here.
            category: count
            platforms:
              predictrz: some-ticker
        """,
    )
    with pytest.raises(ValueError, match="is not known"):
        load_heat_map_questions(yaml_path)


def test_unknown_category_rejected(tmp_path: Path) -> None:
    yaml_path = _write_yaml(
        tmp_path,
        _BASE_HEADER
        + """\
          - id: bad-category
            short_label: Bad
            long_label: Some long description here.
            category: weather
            platforms:
              kalshi: SOME-TICKER
        """,
    )
    with pytest.raises(ValueError, match="not in the known set"):
        load_heat_map_questions(yaml_path)


def test_empty_platforms_rejected(tmp_path: Path) -> None:
    yaml_path = _write_yaml(
        tmp_path,
        _BASE_HEADER
        + """\
          - id: empty-platforms
            short_label: Empty
            long_label: A question with no platform mappings.
            category: count
            platforms: {}
        """,
    )
    with pytest.raises(ValueError, match="at least one platform"):
        load_heat_map_questions(yaml_path)


def test_empty_ticker_rejected(tmp_path: Path) -> None:
    """Empty ticker string is a silent-failure shape — the DB lookup
    returns no rows and the cell renders as if the platform doesn't
    carry the question. Reject upfront."""
    yaml_path = _write_yaml(
        tmp_path,
        _BASE_HEADER
        + """\
          - id: empty-ticker
            short_label: Empty
            long_label: A question with a blank ticker.
            category: count
            platforms:
              kalshi: "  "
        """,
    )
    with pytest.raises(ValueError, match="non-empty ticker"):
        load_heat_map_questions(yaml_path)


def test_duplicate_question_id_rejected(tmp_path: Path) -> None:
    yaml_path = _write_yaml(
        tmp_path,
        _BASE_HEADER
        + """\
          - id: dup-id
            short_label: First
            long_label: First question with this id.
            category: count
            platforms:
              kalshi: TICKER-A
          - id: dup-id
            short_label: Second
            long_label: Second question with the same id.
            category: count
            platforms:
              kalshi: TICKER-B
        """,
    )
    with pytest.raises(ValueError, match="duplicate canonical question id"):
        load_heat_map_questions(yaml_path)


def test_no_questions_rejected(tmp_path: Path) -> None:
    yaml_path = tmp_path / "empty.yaml"
    yaml_path.write_text(
        dedent(
            """\
            version: 1
            last_reviewed: 2026-06-15
            questions: []
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="at least one canonical question"):
        load_heat_map_questions(yaml_path)


def test_invalid_id_slug_rejected(tmp_path: Path) -> None:
    """IDs must be lowercase alphanumeric + hyphens — keeps JSON keys
    and URL fragments stable. An uppercase character or underscore
    means someone forgot the convention."""
    yaml_path = _write_yaml(
        tmp_path,
        _BASE_HEADER
        + """\
          - id: Bad_ID
            short_label: Bad
            long_label: An ID that violates slug rules.
            category: count
            platforms:
              kalshi: TICKER
        """,
    )
    with pytest.raises(ValueError, match="String should match pattern"):
        load_heat_map_questions(yaml_path)


def test_short_label_too_long_rejected(tmp_path: Path) -> None:
    """Short labels render in the column header; >24 chars would
    overflow on standard dashboard widths. Better to fail at load than
    visually break the grid."""
    yaml_path = _write_yaml(
        tmp_path,
        _BASE_HEADER
        + """\
          - id: long-label
            short_label: This label is far too long for the column header
            long_label: A question whose short label overflows.
            category: count
            platforms:
              kalshi: TICKER
        """,
    )
    with pytest.raises(ValueError, match="at most 24"):
        load_heat_map_questions(yaml_path)


# ----- Derived helpers ----------------------------------------------------


def test_link_for_returns_ticker_when_present() -> None:
    doc = load_heat_map_questions()
    q = next(q for q in doc.questions if q.id == "atlantic-count-ge-5")
    assert q.link_for("kalshi") == "KXHURCTOT-26DEC01-T5"


def test_link_for_returns_none_when_platform_absent() -> None:
    """The Kalshi-only entry must return None for Polymarket — that's
    how the heat-map service knows to render an empty cell."""
    doc = load_heat_map_questions()
    q = next(q for q in doc.questions if q.id == "atlantic-count-ge-7")
    assert q.link_for("polymarket") is None


def test_platforms_present_orders_kalshi_first() -> None:
    """Stable, deterministic row ordering. Kalshi was the original
    integration so it sits at the top of the heat-map."""
    doc = load_heat_map_questions()
    platforms = doc.platforms_present()
    assert platforms[0] == "kalshi"
    assert "polymarket" in platforms


def test_questions_for_platform_filters_correctly() -> None:
    """A platform's questions slice is the union of canonical questions
    that list it in platforms — used by the service to issue per-
    platform DB lookups."""
    doc = load_heat_map_questions()
    kalshi_qs = doc.questions_for_platform("kalshi")
    polymarket_qs = doc.questions_for_platform("polymarket")
    # Kalshi carries count-ge-5 and count-ge-7 (per the seed). It does
    # not carry first-hurricane-by-aug-1.
    kalshi_ids = {q.id for q in kalshi_qs}
    assert "atlantic-count-ge-5" in kalshi_ids
    assert "atlantic-count-ge-7" in kalshi_ids
    assert "first-hurricane-by-aug-1" not in kalshi_ids
    # Polymarket carries count-ge-5 and first-hurricane-by-aug-1 but
    # not count-ge-7.
    polymarket_ids = {q.id for q in polymarket_qs}
    assert "atlantic-count-ge-5" in polymarket_ids
    assert "first-hurricane-by-aug-1" in polymarket_ids
    assert "atlantic-count-ge-7" not in polymarket_ids
