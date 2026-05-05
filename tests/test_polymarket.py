"""Unit tests for ``rmn_dashboard.scrapers.polymarket``.

Day 37 — Polymarket Gamma API scraper. Tests use ``httpx.MockTransport``
so the network is never touched. Sample fixture markets are derived from
the real shape Day 36's probe script returned (see
``scripts/probe_polymarket.py`` and the chat-pasted probe output for
ground truth on field names and value formats).
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

# A trimmed-down version of the JSON dump Day 36's probe surfaced for
# the "Will a hurricane make landfall in the US by May 31?" market.
# Includes every field the scraper actually consumes plus a couple of
# dummies to mimic the long surface area of the real response.
_HURRICANE_MARKET = {
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
    "events": [
        {
            "slug": "will-a-hurricane-make-landfall-in-the-us-by-may-31",
            "openInterest": 2770.31,
        }
    ],
}

_NON_HURRICANE_MARKET = {
    "id": "999999",
    "question": "Will the Lakers win the championship?",
    "slug": "lakers-championship",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.30", "0.70"]',
    "volumeNum": 5000.0,
    "volume24hr": 200.0,
    "endDate": "2026-06-30T00:00:00Z",
    "active": True,
    "closed": False,
    "events": [],
}


def _mock_client(handler: Any) -> PolymarketClient:
    """Build a PolymarketClient backed by an ``httpx.MockTransport``."""
    transport = httpx.MockTransport(handler)
    return PolymarketClient(httpx.Client(transport=transport, base_url="https://test"))


def test_fetch_hurricane_markets_filters_to_hurricane_keywords() -> None:
    """The keyword regex matches hurricane / tropical / cyclone / landfall.
    Non-hurricane markets in the same response should be dropped."""

    def handler(request: httpx.Request) -> httpx.Response:
        # First page returns mixed markets; second page returns empty
        # so pagination terminates without sleeping in test loop.
        if "offset=200" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[_HURRICANE_MARKET, _NON_HURRICANE_MARKET])

    client = _mock_client(handler)
    markets = fetch_hurricane_markets(client=client)

    assert len(markets) == 1
    assert markets[0].title == "Will a hurricane make landfall in the US by May 31?"


def test_fetch_hurricane_markets_normalizes_fields_correctly() -> None:
    """Spot-check the parse: prices come from JSON-encoded string list,
    volume from ``volumeNum``, OI from ``events[0].openInterest``, URL
    from ``polymarket.com/event/{slug}``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "offset=200" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[_HURRICANE_MARKET])

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
    the result (the keyword filter still matches), but with None prices.
    The ingest task can decide whether to skip None-priced rows; the
    scraper's contract is "best effort, log warnings, don't crash."
    """
    bad = {**_HURRICANE_MARKET, "outcomePrices": "not-json"}

    def handler(request: httpx.Request) -> httpx.Response:
        if "offset=200" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[bad])

    client = _mock_client(handler)
    markets = fetch_hurricane_markets(client=client)

    assert len(markets) == 1
    assert markets[0].yes_price is None
    assert markets[0].no_price is None


def test_fetch_hurricane_markets_returns_empty_when_no_keyword_hits() -> None:
    """Off-season the basin can be empty. fetch_hurricane_markets returns
    [] rather than raising — the ingest task logs and skips the commit."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "offset=200" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[_NON_HURRICANE_MARKET])

    client = _mock_client(handler)
    markets = fetch_hurricane_markets(client=client)

    assert markets == []


def test_fetch_hurricane_markets_handles_payload_with_markets_envelope() -> None:
    """Polymarket's /markets sometimes returns a bare list, sometimes an
    object with a `markets` key. The scraper handles both shapes."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "offset=200" in str(request.url):
            return httpx.Response(200, json={"markets": []})
        return httpx.Response(200, json={"markets": [_HURRICANE_MARKET]})

    client = _mock_client(handler)
    markets = fetch_hurricane_markets(client=client)

    assert len(markets) == 1
    assert markets[0].ticker == "will-a-hurricane-make-landfall-in-the-us-by-may-31"


def test_fetch_hurricane_markets_skips_markets_without_slug() -> None:
    """Without a slug we can't build a URL; safer to skip than ship a
    half-broken row."""
    no_slug = {k: v for k, v in _HURRICANE_MARKET.items() if k != "slug"}

    def handler(request: httpx.Request) -> httpx.Response:
        if "offset=200" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[no_slug])

    client = _mock_client(handler)
    markets = fetch_hurricane_markets(client=client)

    assert markets == []


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

    def handler(request: httpx.Request) -> httpx.Response:
        if "offset=200" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[multi])

    client = _mock_client(handler)
    markets = fetch_hurricane_markets(client=client)

    assert len(markets) == 1
    # First two parsed as Yes/No; third element ignored by design.
    assert markets[0].yes_price == pytest.approx(0.4)
    assert markets[0].no_price == pytest.approx(0.5)


def test_fetch_hurricane_markets_query_params_include_filters() -> None:
    """Defensive: confirm the scraper sends closed=false and archived=false
    so we don't accidentally pull resolved markets into the panel."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if "offset=200" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[])

    client = _mock_client(handler)
    fetch_hurricane_markets(client=client)

    assert captured, "scraper made no requests"
    first = captured[0]
    qs = dict(first.url.params)
    assert qs.get("closed") == "false"
    assert qs.get("archived") == "false"


def test_outcome_prices_round_trip_with_smart_quotes_does_not_crash() -> None:
    """Defensive against a hypothetical Polymarket API change that swaps
    ASCII quotes in the JSON-encoded string for typographic ones (which
    would break json.loads). Should return None prices, not raise."""
    weird = {
        **_HURRICANE_MARKET,
        "outcomePrices": "[“0.4”, “0.6”]",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "offset=200" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[weird])

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

    def handler(request: httpx.Request) -> httpx.Response:
        if "offset=200" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[no_outcomes])

    client = _mock_client(handler)
    markets = fetch_hurricane_markets(client=client)

    assert len(markets) == 1
    assert markets[0].yes_price == pytest.approx(0.0195)


# Sanity check: the fixture's outcomes JSON parses cleanly too.
def test_fixture_outcomes_field_is_valid_json() -> None:
    """Guard against accidentally writing a malformed test fixture."""
    parsed = json.loads(_HURRICANE_MARKET["outcomes"])
    assert parsed == ["Yes", "No"]
