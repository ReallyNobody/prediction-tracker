"""Unit tests for ``rmn_dashboard.scrapers.polymarket``.

June 2026 — rewritten when the scraper switched from
``/markets?closed=false`` (with a client-side keyword regex) to
``/events?tag_slug=hurricane`` (Polymarket's curated hurricane tag).
The inner market dict shape is identical between the two endpoints,
so the parse + normalize assertions carry over; what changes is the
response envelope: tests now mock event objects with nested
``markets`` arrays, mirroring the live Gamma API shape.

Tests use ``httpx.MockTransport`` so the network is never touched.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from rmn_dashboard.scrapers.polymarket import (
    PolymarketClient,
    fetch_hurricane_markets,
)

# Inner market dict — same shape Polymarket has always reported,
# both under the old /markets endpoint and under each event's nested
# ``markets`` array on the new /events endpoint. Derived from real
# Day 36 probe data plus what the June 2026 live API confirmed.
_HURRICANE_MARKET: dict[str, Any] = {
    "id": "820350",
    "question": "Will a hurricane make landfall in the US by May 31?",
    "slug": "will-a-hurricane-make-landfall-in-the-us-by-may-31",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.0195", "0.9805"]',
    "volume": "17317.78",
    "volumeNum": 17317.78,
    "volume24hr": 61.02,
    "liquidity": "1751.89",
    "liquidityNum": 1751.89,
    "endDate": "2026-05-31T00:00:00Z",
    "active": True,
    "closed": False,
}


def _wrap_event(
    *markets: dict[str, Any],
    slug: str = "will-a-hurricane-make-landfall-in-the-us-by-may-31",
    ticker: str | None = None,
    open_interest: float | None = 2770.31,
) -> dict[str, Any]:
    """Build one event envelope wrapping the given child markets.

    Mirrors the shape returned by /events?tag_slug=hurricane in the
    live API: parent-level slug + ticker + openInterest, with a
    nested ``markets`` array.
    """
    return {
        "id": "131388",
        "ticker": ticker or slug,
        "slug": slug,
        "title": "Will a hurricane make landfall in the US by May 31?",
        "active": True,
        "closed": False,
        "archived": False,
        "openInterest": open_interest,
        "markets": list(markets),
    }


def _mock_client(handler: Any) -> PolymarketClient:
    """Build a PolymarketClient backed by an ``httpx.MockTransport``."""
    transport = httpx.MockTransport(handler)
    return PolymarketClient(httpx.Client(transport=transport, base_url="https://test"))


# ----- Endpoint contract --------------------------------------------------


def test_fetch_hurricane_markets_uses_events_tag_slug_endpoint() -> None:
    """The scraper must hit /events?tag_slug=hurricane — querying the
    untagged /markets catalog was the silent-failure mode that broke
    Polymarket ingest in May 2026."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[])

    client = _mock_client(handler)
    fetch_hurricane_markets(client=client)

    assert captured, "scraper made no requests"
    request = captured[0]
    assert request.url.path == "/events"
    params = dict(request.url.params)
    assert params.get("tag_slug") == "hurricane"
    assert params.get("closed") == "false"
    assert params.get("archived") == "false"


# ----- Normalization ------------------------------------------------------


def test_fetch_hurricane_markets_normalizes_fields_correctly() -> None:
    """Spot-check the parse: prices come from JSON-encoded string list,
    volume from ``volumeNum``, OI from the parent event injected into
    ``events[0].openInterest``, URL from ``polymarket.com/event/{slug}``."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_wrap_event(_HURRICANE_MARKET)])

    client = _mock_client(handler)
    markets = fetch_hurricane_markets(client=client)

    assert len(markets) == 1
    m = markets[0]
    assert m.platform == "polymarket"
    assert m.ticker == "will-a-hurricane-make-landfall-in-the-us-by-may-31"
    assert m.yes_price == pytest.approx(0.0195)
    assert m.no_price == pytest.approx(0.9805)
    assert m.volume_total == pytest.approx(17317.78)
    assert m.volume_24h == pytest.approx(61.02)
    assert m.open_interest == pytest.approx(2770.31)
    assert m.close_time == "2026-05-31T00:00:00Z"
    assert m.url == (
        "https://polymarket.com/event/will-a-hurricane-make-landfall-in-the-us-by-may-31"
    )


def test_fetch_hurricane_markets_handles_unparseable_outcome_prices() -> None:
    """A market with malformed ``outcomePrices`` should still appear in
    the result (the event-tag filter passed), but with None prices.
    The ingest task can decide whether to skip None-priced rows; the
    scraper's contract is "best effort, log warnings, don't crash."
    """
    bad = {**_HURRICANE_MARKET, "outcomePrices": "not-json"}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_wrap_event(bad)])

    client = _mock_client(handler)
    markets = fetch_hurricane_markets(client=client)

    assert len(markets) == 1
    assert markets[0].yes_price is None
    assert markets[0].no_price is None


# ----- Empty / shape handling --------------------------------------------


def test_fetch_hurricane_markets_returns_empty_when_no_events() -> None:
    """Off-season the tag can be empty. fetch_hurricane_markets returns
    [] rather than raising — the ingest task logs and skips the commit."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = _mock_client(handler)
    markets = fetch_hurricane_markets(client=client)

    assert markets == []


def test_fetch_hurricane_markets_handles_events_envelope_payload() -> None:
    """Polymarket's /events endpoint usually returns a bare list, but
    has historically returned an ``{events: [...]}`` envelope in some
    paths. Both shapes parse."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"events": [_wrap_event(_HURRICANE_MARKET)]})

    client = _mock_client(handler)
    markets = fetch_hurricane_markets(client=client)

    assert len(markets) == 1


def test_fetch_hurricane_markets_handles_event_with_no_markets_array() -> None:
    """An event with no ``markets`` field (or an empty array) yields
    no output — skip the event silently."""
    empty_event = {
        "id": "131388",
        "slug": "empty-event",
        "title": "Empty Event",
        "markets": [],
    }

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[empty_event])

    client = _mock_client(handler)
    markets = fetch_hurricane_markets(client=client)

    assert markets == []


# ----- Flatten + per-market filtering ------------------------------------


def test_fetch_hurricane_markets_flattens_multiple_markets_per_event() -> None:
    """An event with N child markets emits N normalized records — each
    inheriting the parent event's openInterest."""
    market_a = {**_HURRICANE_MARKET, "slug": "child-a", "question": "Child A?"}
    market_b = {**_HURRICANE_MARKET, "slug": "child-b", "question": "Child B?"}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[_wrap_event(market_a, market_b, open_interest=500.0)],
        )

    client = _mock_client(handler)
    markets = fetch_hurricane_markets(client=client)

    tickers = {m.ticker for m in markets}
    assert tickers == {"child-a", "child-b"}
    # Both inherit the parent event's openInterest.
    assert all(m.open_interest == pytest.approx(500.0) for m in markets)


def test_fetch_hurricane_markets_skips_closed_markets_in_event() -> None:
    """A child market with ``closed=true`` is filtered out, even when
    its parent event is still active."""
    closed = {**_HURRICANE_MARKET, "slug": "closed-child", "closed": True}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_wrap_event(closed)])

    client = _mock_client(handler)
    markets = fetch_hurricane_markets(client=client)

    assert markets == []


def test_fetch_hurricane_markets_skips_inactive_markets_in_event() -> None:
    """A child market with ``active=false`` is filtered out."""
    inactive = {**_HURRICANE_MARKET, "slug": "inactive-child", "active": False}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_wrap_event(inactive)])

    client = _mock_client(handler)
    markets = fetch_hurricane_markets(client=client)

    assert markets == []


def test_fetch_hurricane_markets_skips_markets_without_slug() -> None:
    """Without a slug we can't build a URL; safer to skip than ship a
    half-broken row."""
    no_slug = {k: v for k, v in _HURRICANE_MARKET.items() if k != "slug"}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_wrap_event(no_slug)])

    client = _mock_client(handler)
    markets = fetch_hurricane_markets(client=client)

    assert markets == []


# ----- Outcome-prices parser ---------------------------------------------


def test_outcome_prices_parser_handles_three_outcome_markets() -> None:
    """Polymarket has multi-outcome markets too (e.g. a 3-way race).
    Hurricane markets in our universe are all binary, but the parser
    should at minimum not crash on a 3-element list — it just returns
    the first two as Yes/No."""
    multi = {
        **_HURRICANE_MARKET,
        "outcomes": '["Yes", "No", "Maybe"]',
        "outcomePrices": '["0.4", "0.5", "0.1"]',
    }

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_wrap_event(multi)])

    client = _mock_client(handler)
    markets = fetch_hurricane_markets(client=client)

    assert len(markets) == 1
    # First two parsed as Yes/No; third element ignored by design.
    assert markets[0].yes_price == pytest.approx(0.4)
    assert markets[0].no_price == pytest.approx(0.5)


def test_outcome_prices_round_trip_with_smart_quotes_does_not_crash() -> None:
    """Defensive against a hypothetical Polymarket API change that swaps
    ASCII quotes in the JSON-encoded string for typographic ones (which
    would break json.loads). Should return None prices, not raise."""
    weird = {
        **_HURRICANE_MARKET,
        "outcomePrices": "[“0.4”, “0.6”]",
    }

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_wrap_event(weird)])

    client = _mock_client(handler)
    markets = fetch_hurricane_markets(client=client)

    assert len(markets) == 1
    assert markets[0].yes_price is None
    assert markets[0].no_price is None


def test_outcome_prices_parser_raw_unit() -> None:
    """Direct unit test of the JSON-string parser — independent of the
    full fetch pipeline so a regression in just this function is easier
    to localize."""
    from rmn_dashboard.scrapers.polymarket import _parse_outcome_prices

    yes, no = _parse_outcome_prices('["0.30", "0.70"]')
    assert yes == pytest.approx(0.30)
    assert no == pytest.approx(0.70)

    # Empty / None / malformed all return (None, None).
    assert _parse_outcome_prices(None) == (None, None)
    assert _parse_outcome_prices("") == (None, None)
    assert _parse_outcome_prices("not-json") == (None, None)
    assert _parse_outcome_prices("[]") == (None, None)
    assert _parse_outcome_prices('["only-one"]') == (None, None)


def test_to_polymarket_url_uses_event_path() -> None:
    """``/event/{slug}`` is the canonical URL we publish, not ``/market/``.
    Verified Day 36 against polymarket.com — both work but /market/
    302-redirects to /event/, so we save a hop by using /event/ directly."""
    from rmn_dashboard.scrapers.polymarket import _to_polymarket_url

    assert _to_polymarket_url("foo-bar") == "https://polymarket.com/event/foo-bar"


def test_outcomes_field_unused_but_harmless() -> None:
    """The scraper consumes ``outcomePrices`` but not ``outcomes``. A
    market missing ``outcomes`` entirely should still parse cleanly as
    long as outcomePrices is present."""
    no_outcomes = {k: v for k, v in _HURRICANE_MARKET.items() if k != "outcomes"}
    # Sanity: still has outcomePrices.
    assert "outcomePrices" in no_outcomes

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_wrap_event(no_outcomes)])

    client = _mock_client(handler)
    markets = fetch_hurricane_markets(client=client)

    assert len(markets) == 1
    assert markets[0].yes_price == pytest.approx(0.0195)


def test_fixture_outcomes_field_is_valid_json() -> None:
    """Guard against accidentally writing a malformed test fixture."""
    parsed = json.loads(_HURRICANE_MARKET["outcomes"])
    assert parsed == ["Yes", "No"]
