"""Tests for the yfinance scraper module.

Network is never touched: every test passes a fake ``fetch_one`` that
returns canned dicts (or None) so we exercise the contract without
depending on Yahoo's uptime.

What we lock down:

  * The snapshot dataclass is built correctly from the fetcher's
    payload — including the derived change_amount / change_percent.
  * Per-ticker fetch failures (returning None or missing last_price)
    log-and-skip without aborting the batch.
  * NaN / missing prior_close edges don't blow up the change math.
  * Every returned snapshot shares the same ``fetched_at`` so a batch
    reads as one logical scrape (the read-side service joins on
    MAX(as_of) per ticker; ragged timestamps would split the "latest").
"""

from __future__ import annotations

from datetime import UTC, datetime
from textwrap import dedent

import pytest

from rmn_dashboard.data.universe import load_universe
from rmn_dashboard.scrapers.yfinance_quotes import (
    QuoteSnapshot,
    _to_snapshot,
    fetch_universe_quotes,
)


@pytest.fixture
def tiny_universe(tmp_path):
    """A 3-ticker universe so we don't depend on the bundled file's roster."""
    body = dedent(
        """\
        version: 1
        last_reviewed: 2026-04-24
        tickers:
          - ticker: UVE
            name: Universal Insurance Holdings
            sector: insurer
            key_states: [FL]
            hurricane_relevance: high
          - ticker: NEE
            name: NextEra Energy
            sector: utility
            key_states: [FL]
            hurricane_relevance: high
          - ticker: HCI
            name: HCI Group
            sector: insurer
            key_states: [FL]
            hurricane_relevance: high
        """
    )
    path = tmp_path / "tiny.yaml"
    path.write_text(body, encoding="utf-8")
    load_universe.cache_clear()
    return load_universe(path)


# ----- _to_snapshot unit tests ------------------------------------------


def test_to_snapshot_computes_change_when_prior_close_present() -> None:
    fetched_at = datetime(2026, 4, 24, 16, 30, tzinfo=UTC)
    info = {
        "last_price": 21.45,
        "previous_close": 20.00,
        "last_volume": 1_234_567,
        "market_cap": 651_000_000,
        "currency": "USD",
    }
    snap = _to_snapshot("UVE", info, fetched_at=fetched_at)

    assert snap.ticker == "UVE"
    assert snap.last_price == 21.45
    assert snap.prior_close == 20.00
    assert snap.change_amount == pytest.approx(1.45)
    assert snap.change_percent == pytest.approx(7.25)
    assert snap.volume == 1_234_567
    assert snap.market_cap == 651_000_000
    assert snap.currency == "USD"
    assert snap.fetched_at == fetched_at
    assert snap.source == "yfinance"


def test_to_snapshot_handles_missing_prior_close_without_division_by_zero() -> None:
    """Some yfinance responses omit previous_close — change must stay None."""
    snap = _to_snapshot(
        "HG",
        {"last_price": 18.50, "previous_close": None},
        fetched_at=datetime.now(UTC),
    )
    assert snap.last_price == 18.50
    assert snap.prior_close is None
    assert snap.change_amount is None
    assert snap.change_percent is None


def test_to_snapshot_handles_zero_prior_close() -> None:
    """Pathological prior_close=0 (rare; happens around ticker
    re-listings) shouldn't ZeroDivisionError — change just stays None."""
    snap = _to_snapshot(
        "XYZ",
        {"last_price": 5.00, "previous_close": 0.0},
        fetched_at=datetime.now(UTC),
    )
    assert snap.change_amount is None
    assert snap.change_percent is None


# ----- fetch_universe_quotes ------------------------------------------


def test_fetch_universe_quotes_returns_snapshot_per_ticker(tiny_universe) -> None:
    fixed_now = datetime(2026, 4, 24, 17, 0, tzinfo=UTC)

    canned = {
        "UVE": {"last_price": 21.0, "previous_close": 20.0, "last_volume": 100, "currency": "USD"},
        "NEE": {"last_price": 80.0, "previous_close": 81.0, "last_volume": 500, "currency": "USD"},
        "HCI": {"last_price": 110.0, "previous_close": 105.0, "last_volume": 80, "currency": "USD"},
    }

    def fake_fetch(ticker: str) -> dict | None:
        return canned.get(ticker)

    snapshots = fetch_universe_quotes(
        universe=tiny_universe, fetch_one=fake_fetch, fetched_at=fixed_now
    )

    assert len(snapshots) == 3
    by_ticker = {s.ticker: s for s in snapshots}
    assert by_ticker["UVE"].change_amount == pytest.approx(1.0)
    assert by_ticker["NEE"].change_amount == pytest.approx(-1.0)
    # All snapshots stamped with the same fetched_at — a single logical scrape.
    assert {s.fetched_at for s in snapshots} == {fixed_now}


def test_fetch_universe_quotes_skips_failed_tickers(tiny_universe, caplog) -> None:
    """A None return from ``fetch_one`` (network glitch, delisted, etc.)
    is logged-and-skipped; the rest of the batch persists.
    """

    def fake_fetch(ticker: str) -> dict | None:
        if ticker == "NEE":
            return None  # simulate yfinance hiccup
        return {"last_price": 99.0, "previous_close": 98.0}

    snapshots = fetch_universe_quotes(universe=tiny_universe, fetch_one=fake_fetch)

    returned = {s.ticker for s in snapshots}
    assert returned == {"UVE", "HCI"}  # NEE skipped, others present


def test_fetch_universe_quotes_skips_missing_last_price(tiny_universe) -> None:
    """A returned dict with no last_price is also a skip — the only
    measurement that's mandatory on the persisted row.
    """

    def fake_fetch(ticker: str) -> dict | None:
        if ticker == "UVE":
            return {"last_price": None, "previous_close": 20.0}
        return {"last_price": 50.0, "previous_close": 49.0}

    snapshots = fetch_universe_quotes(universe=tiny_universe, fetch_one=fake_fetch)
    returned = {s.ticker for s in snapshots}
    assert "UVE" not in returned
    assert returned == {"NEE", "HCI"}


def test_fetch_universe_quotes_uses_default_universe_when_none_passed(monkeypatch) -> None:
    """When no universe is passed, we fall back to the bundled YAML.

    Smoke-test only — we don't pull every ticker, just confirm that
    leaving universe=None loads at least some snapshots through the
    fake fetcher.
    """

    def fake_fetch(ticker: str) -> dict | None:
        return {"last_price": 1.0, "previous_close": 1.0}

    load_universe.cache_clear()
    snapshots = fetch_universe_quotes(fetch_one=fake_fetch)
    # Every ticker in the bundled universe has a snapshot.
    assert len(snapshots) >= 20
    assert all(isinstance(s, QuoteSnapshot) for s in snapshots)
