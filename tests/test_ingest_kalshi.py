"""Unit tests for the Kalshi ingestion task.

These tests never touch the network: ``fetch_hurricane_markets`` is a pure
function over a ``KalshiClient``, so we can hand it a client backed by an
``httpx.MockTransport`` and assert on what lands in the DB.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from rmn_dashboard.models import PredictionMarket
from rmn_dashboard.scrapers.kalshi import KalshiClient
from rmn_dashboard.tasks.ingest_kalshi import (
    _market_to_row,
    _to_close_date,
    run_kalshi_ingest,
)

# ----- Shared stubs (mirror test_kalshi.py; kept local so this file is
# self-contained and doesn't reach across into another test module) ----------


@dataclass
class _StubSigner:
    signed: list[bytes] = field(default_factory=list)

    def sign(self, data: bytes, _pad: Any, _algo: Any) -> bytes:
        self.signed.append(data)
        return b"\x00" * 32


def _make_client(handler: Callable[[httpx.Request], httpx.Response]) -> KalshiClient:
    transport = httpx.MockTransport(handler)
    return KalshiClient(
        api_key_id="test-key",
        private_key=_StubSigner(),
        base_url="https://api.example.com/trade-api/v2",
        http_client=httpx.Client(transport=transport),
    )


# ----- Pure-function unit tests --------------------------------------------


def test_to_close_date_parses_iso_z_suffix() -> None:
    d = _to_close_date("2026-12-01T00:00:00Z")
    assert d is not None
    assert d.year == 2026
    assert d.month == 12
    assert d.day == 1


def test_to_close_date_returns_none_on_junk() -> None:
    assert _to_close_date(None) is None
    assert _to_close_date("") is None
    assert _to_close_date("not a date") is None


def test_market_to_row_maps_fields_and_derives_no_price() -> None:
    from rmn_dashboard.scrapers.kalshi import KalshiMarket

    km = KalshiMarket(
        platform="Kalshi",
        series_ticker="KXHURCTOT",
        event_ticker="KXHURCTOT-26DEC01",
        ticker="KXHURCTOT-26DEC01-T7",
        title="Will there be more than 7 Atlantic hurricanes in 2026?",
        subtitle=None,
        yes_bid=0.41,
        no_bid=0.42,
        yes_ask=0.58,
        no_ask=0.59,
        last_price=0.5,
        volume_24h=3.0,
        volume_total=1234.0,
        open_interest=269.0,
        close_time="2026-12-01T00:00:00Z",
        url="https://kalshi.com/markets/KXHURCTOT-26DEC01-T7",
    )
    row = _market_to_row(km)

    assert row.platform == "kalshi"
    assert row.ticker == "KXHURCTOT-26DEC01-T7"
    assert row.event_ticker == "KXHURCTOT-26DEC01"
    assert row.category == "hurricane"
    assert row.yes_price == 0.5
    assert row.no_price == 0.5  # derived as 1 - last_price
    assert row.open_interest == 269.0
    assert row.close_date is not None
    assert row.close_date.year == 2026


# ----- End-to-end ingest (mock transport + real SQLite) --------------------

_SAMPLE_MARKETS = [
    {
        "ticker": "KXHURCTOT-26DEC01-T7",
        "event_ticker": "KXHURCTOT-26DEC01",
        "title": "Will there be more than 7 Atlantic hurricanes in 2026?",
        "last_price_dollars": "0.5",
        "yes_bid_dollars": "0.41",
        "no_bid_dollars": "0.42",
        "yes_ask_dollars": "0.58",
        "no_ask_dollars": "0.59",
        "volume_24h_fp": "3.0",
        "volume_fp": "1234.0",
        "open_interest_fp": "269.0",
        "close_time": "2026-12-01T00:00:00Z",
    },
    {
        "ticker": "KXHURCTOT-26DEC01-T5",
        "event_ticker": "KXHURCTOT-26DEC01",
        "title": "Will there be more than 5 Atlantic hurricanes in 2026?",
        "last_price_dollars": "0.75",
        "open_interest_fp": "80.0",
        "close_time": "2026-12-01T00:00:00Z",
    },
]


def test_run_kalshi_ingest_persists_snapshots(db_session: Session) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/markets")
        series = request.url.params.get("series_ticker")
        # Only return markets for KXHURCTOT in this test; other series empty.
        if series == "KXHURCTOT":
            return httpx.Response(200, json={"markets": _SAMPLE_MARKETS})
        return httpx.Response(200, json={"markets": []})

    client = _make_client(handler)
    count = run_kalshi_ingest(
        db_session,
        series_tickers=["KXHURCTOT", "KXHURCTOTMAJ"],
        client=client,
    )

    assert count == 2
    rows = list(db_session.scalars(select(PredictionMarket)).all())
    assert len(rows) == 2
    assert {r.ticker for r in rows} == {"KXHURCTOT-26DEC01-T7", "KXHURCTOT-26DEC01-T5"}
    assert all(r.platform == "kalshi" and r.category == "hurricane" for r in rows)


def test_run_kalshi_ingest_returns_zero_when_no_markets(db_session: Session) -> None:
    """Empty upstream → nothing persisted, no commit, count=0."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"markets": []})

    client = _make_client(handler)
    count = run_kalshi_ingest(db_session, series_tickers=["KXHURCTOT"], client=client)

    assert count == 0
    assert db_session.scalar(select(PredictionMarket)) is None


def test_run_kalshi_ingest_skips_rows_without_ticker(db_session: Session) -> None:
    """Guard rail: a malformed upstream record missing a ticker shouldn't
    land in the DB with an empty primary-key-ish field."""

    malformed = [
        {"ticker": "KXHURCTOT-26DEC01-T7", "last_price_dollars": "0.5"},
        {"ticker": None, "last_price_dollars": "0.3"},  # skip me
        {"ticker": "", "last_price_dollars": "0.2"},  # skip me too
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"markets": malformed})

    client = _make_client(handler)
    count = run_kalshi_ingest(db_session, series_tickers=["KXHURCTOT"], client=client)

    assert count == 1
    rows = list(db_session.scalars(select(PredictionMarket)).all())
    assert [r.ticker for r in rows] == ["KXHURCTOT-26DEC01-T7"]
