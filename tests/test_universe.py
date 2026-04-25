"""Tests for the hurricane equity universe loader.

The bundled YAML is the source of truth for Panel 2; these tests lock
down two things:

  * The shipped file parses + validates cleanly (one assertion against
    the real ``hurricane_universe.yaml`` so a malformed hand-edit fails
    in CI rather than at uvicorn startup).
  * The Pydantic schema catches the editorial mistakes most likely to
    happen on a hand-edit: bad sector tag, bogus state code, duplicate
    ticker, lowercase ticker, malformed CIK.

We use ``tmp_path`` to write tiny fixture YAMLs for the negative cases
so we don't have to mutate the shipped file.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from rmn_dashboard.data.universe import (
    Universe,
    filter_by_sector,
    load_universe,
    tickers_for_states,
)


@pytest.fixture(autouse=True)
def _clear_universe_cache() -> None:
    """Each test gets a fresh load — the loader is module-level cached
    by path, so a fixture that mutates the file system between tests
    needs the cache reset to actually re-read.
    """
    load_universe.cache_clear()


def _write_yaml(path: Path, body: str) -> Path:
    target = path / "universe.yaml"
    target.write_text(dedent(body), encoding="utf-8")
    return target


# ----- Bundled-file smoke tests ------------------------------------------


def test_bundled_universe_loads_cleanly() -> None:
    """The shipped YAML round-trips through validation without error.

    Catches typos / schema drift the moment a contributor commits a
    bad hand-edit; this single test stands in for "we don't ship a
    broken universe to prod."
    """
    universe = load_universe()
    assert isinstance(universe, Universe)
    # Sanity: we expect ~35 tickers in the launch roster. Don't pin the
    # exact number — that becomes a friction tax on legitimate edits.
    assert 20 <= len(universe.tickers) <= 80
    # Every sector represented at least once — drives the Panel 2 filter pills.
    sectors = {entry.sector for entry in universe.tickers}
    assert sectors == {"insurer", "reinsurer", "homebuilder", "utility"}


def test_bundled_universe_has_no_duplicate_tickers() -> None:
    """The duplicate-ticker validator runs at load time; this test is
    explicit so a regression that broke the validator (or a bad merge
    that double-listed a ticker) fails with a name we can grep for.
    """
    universe = load_universe()
    seen = [entry.ticker for entry in universe.tickers]
    assert len(seen) == len(set(seen))


def test_bundled_reinsurers_have_empty_key_states() -> None:
    """Editorial rule: reinsurers carry empty ``key_states`` because
    their books are global and per-state precision is a fiction. Lock
    it down so a future hand-edit that adds states to a reinsurer
    fails this test instead of silently lighting up RNR on a single-
    state cone.
    """
    universe = load_universe()
    reinsurers = filter_by_sector(universe, ["reinsurer"])
    assert reinsurers, "expected at least one reinsurer in the bundled universe"
    for entry in reinsurers:
        assert entry.key_states == (), (
            f"reinsurer {entry.ticker} has key_states={entry.key_states}; "
            "should be empty by editorial convention"
        )


# ----- Schema validation negative cases ----------------------------------


_BASE_HEADER = """\
    version: 1
    last_reviewed: 2026-04-24
    tickers:
"""


def test_load_rejects_invalid_sector(tmp_path: Path) -> None:
    yaml_body = _BASE_HEADER + dedent(
        """\
          - ticker: UVE
            name: Universal Insurance Holdings
            sector: insure
            key_states: [FL]
            hurricane_relevance: high
        """
    )
    target = _write_yaml(tmp_path, yaml_body)
    with pytest.raises(ValueError):
        load_universe(target)


def test_load_rejects_unknown_state_code(tmp_path: Path) -> None:
    yaml_body = _BASE_HEADER + dedent(
        """\
          - ticker: UVE
            name: Universal Insurance Holdings
            sector: insurer
            key_states: [XX]
            hurricane_relevance: high
        """
    )
    target = _write_yaml(tmp_path, yaml_body)
    with pytest.raises(ValueError, match="not a recognized US state code"):
        load_universe(target)


def test_load_rejects_lowercase_ticker(tmp_path: Path) -> None:
    yaml_body = _BASE_HEADER + dedent(
        """\
          - ticker: uve
            name: Universal Insurance Holdings
            sector: insurer
            key_states: [FL]
            hurricane_relevance: high
        """
    )
    target = _write_yaml(tmp_path, yaml_body)
    with pytest.raises(ValueError, match="must be uppercase"):
        load_universe(target)


def test_load_rejects_duplicate_tickers(tmp_path: Path) -> None:
    yaml_body = _BASE_HEADER + dedent(
        """\
          - ticker: UVE
            name: Universal Insurance Holdings
            sector: insurer
            key_states: [FL]
            hurricane_relevance: high
          - ticker: UVE
            name: Universal Insurance (clone)
            sector: insurer
            key_states: [FL]
            hurricane_relevance: high
        """
    )
    target = _write_yaml(tmp_path, yaml_body)
    with pytest.raises(ValueError, match="duplicate ticker"):
        load_universe(target)


def test_load_rejects_malformed_cik(tmp_path: Path) -> None:
    yaml_body = _BASE_HEADER + dedent(
        """\
          - ticker: UVE
            name: Universal Insurance Holdings
            sector: insurer
            cik: "https://sec.gov/cik?CIK=891166"
            key_states: [FL]
            hurricane_relevance: high
        """
    )
    target = _write_yaml(tmp_path, yaml_body)
    with pytest.raises(ValueError, match="must be all digits"):
        load_universe(target)


def test_load_accepts_null_cik_and_filled_cik(tmp_path: Path) -> None:
    """CIK is null in the launch YAML; the discovery job will fill it
    in post-launch. Both forms should validate.
    """
    yaml_body = _BASE_HEADER + dedent(
        """\
          - ticker: UVE
            name: Universal Insurance Holdings
            sector: insurer
            cik: null
            key_states: [FL]
            hurricane_relevance: high
          - ticker: HCI
            name: HCI Group
            sector: insurer
            cik: "0000891166"
            key_states: [FL]
            hurricane_relevance: high
        """
    )
    target = _write_yaml(tmp_path, yaml_body)
    universe = load_universe(target)
    assert universe.tickers[0].cik is None
    assert universe.tickers[1].cik == "0000891166"


# ----- Query helpers -----------------------------------------------------


def test_filter_by_sector_subset() -> None:
    universe = load_universe()
    homebuilders = filter_by_sector(universe, ["homebuilder"])
    assert all(entry.sector == "homebuilder" for entry in homebuilders)
    # Sanity: at least a handful of names — the launch list has 8.
    assert len(homebuilders) >= 5


def test_filter_by_sector_multi() -> None:
    universe = load_universe()
    insurers_and_reinsurers = filter_by_sector(universe, ["insurer", "reinsurer"])
    sectors = {entry.sector for entry in insurers_and_reinsurers}
    assert sectors == {"insurer", "reinsurer"}


def test_tickers_for_states_returns_intersecting_entries() -> None:
    """Florida cone: insurers + homebuilders + utilities tagged FL all
    light up. Reinsurers (empty key_states) never do.
    """
    universe = load_universe()
    fl_exposed = tickers_for_states(universe, ["FL"])
    fl_tickers = {entry.ticker for entry in fl_exposed}

    # FL homeowners specialists must be present.
    assert "UVE" in fl_tickers
    assert "HCI" in fl_tickers
    # FL utility must be present.
    assert "NEE" in fl_tickers
    # No reinsurers should appear (their key_states is empty by design).
    reinsurer_tickers = {e.ticker for e in filter_by_sector(universe, ["reinsurer"])}
    assert reinsurer_tickers.isdisjoint(fl_tickers)


def test_tickers_for_states_is_case_insensitive() -> None:
    universe = load_universe()
    upper = {e.ticker for e in tickers_for_states(universe, ["FL"])}
    lower = {e.ticker for e in tickers_for_states(universe, ["fl"])}
    assert upper == lower


def test_tickers_for_states_empty_input_returns_empty() -> None:
    """An empty cone (no affected states) shouldn't accidentally light
    up the entire universe — defensive check on the early-out.
    """
    universe = load_universe()
    assert tickers_for_states(universe, []) == ()
