"""Unit tests for the Kalshi authenticated scraper.

These tests never hit the network and never use a real RSA key. ``httpx``
requests flow through ``httpx.MockTransport``; signing is handled by a
tiny stub that records its inputs so we can assert on the message that
would have been signed.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from rmn_dashboard.scrapers.kalshi import (
    KalshiClient,
    KalshiConfigError,
    KalshiMarket,
    _sign_request,
    client_from_settings,
    fetch_hurricane_markets,
    load_private_key,
)

# ----- Stubs ---------------------------------------------------------------


@dataclass
class StubSigner:
    """Records every sign() call and returns a deterministic byte string."""

    signed_messages: list[bytes] = field(default_factory=list)
    return_bytes: bytes = b"\x00" * 32

    def sign(self, data: bytes, pad: Any, algo: Any) -> bytes:
        self.signed_messages.append(data)
        return self.return_bytes


def _make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    signer: StubSigner | None = None,
    api_key_id: str = "test-api-key-id",
    base_url: str = "https://api.example.com/trade-api/v2",
) -> tuple[KalshiClient, StubSigner]:
    """Construct a KalshiClient wired to an in-process MockTransport."""
    signer = signer or StubSigner()
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    client = KalshiClient(
        api_key_id=api_key_id,
        private_key=signer,
        base_url=base_url,
        http_client=http,
    )
    return client, signer


# ----- _sign_request -------------------------------------------------------


def test_sign_request_builds_expected_message() -> None:
    signer = StubSigner()
    sig = _sign_request(signer, "1700000000000", "GET", "/trade-api/v2/markets")

    # Signer received exactly the concatenation Kalshi docs specify.
    assert signer.signed_messages == [b"1700000000000GET/trade-api/v2/markets"]
    # Signature is base64 of whatever the signer returned.
    assert sig == base64.b64encode(signer.return_bytes).decode("ascii")


def test_sign_request_strips_query_string() -> None:
    signer = StubSigner()
    _sign_request(signer, "1700000000000", "GET", "/trade-api/v2/markets?series_ticker=X")

    # Query string must be excluded from the signed message.
    assert signer.signed_messages == [b"1700000000000GET/trade-api/v2/markets"]


def test_sign_request_uppercases_method() -> None:
    signer = StubSigner()
    _sign_request(signer, "1700000000000", "get", "/trade-api/v2/markets")

    assert signer.signed_messages == [b"1700000000000GET/trade-api/v2/markets"]


# ----- KalshiClient --------------------------------------------------------


def test_client_rejects_empty_api_key_id() -> None:
    with pytest.raises(KalshiConfigError):
        KalshiClient(api_key_id="", private_key=StubSigner())


def test_client_get_attaches_required_headers() -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"markets": []})

    client, _signer = _make_client(handler, api_key_id="my-key-id")
    client.get("/markets", params={"series_ticker": "X", "status": "active"})

    req = captured["request"]
    assert req.headers["KALSHI-ACCESS-KEY"] == "my-key-id"
    assert req.headers["KALSHI-ACCESS-SIGNATURE"]
    assert req.headers["KALSHI-ACCESS-TIMESTAMP"].isdigit()


def test_client_get_signs_the_full_api_path() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"markets": []})

    client, signer = _make_client(handler, base_url="https://api.example.com/trade-api/v2")
    client.get("/markets", params={"series_ticker": "X"})

    # One signing call — over the full path, without the query string.
    assert len(signer.signed_messages) == 1
    signed = signer.signed_messages[0].decode("ascii")
    assert signed.endswith("GET/trade-api/v2/markets")
    # Query string must not appear in the signed message.
    assert "series_ticker" not in signed


def test_client_raises_on_http_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    client, _ = _make_client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.get("/markets")


# ----- fetch_hurricane_markets --------------------------------------------


_SAMPLE_RAW_MARKET = {
    "event_ticker": "HURR-ATLANTIC",
    "ticker": "HURR-ATLANTIC-NUM-5PLUS",
    "title": "Will 5 or more Atlantic hurricanes form in 2026?",
    "subtitle": "Atlantic basin, named hurricanes",
    "yes_bid_dollars": "0.62",
    "no_bid_dollars": "0.37",
    "yes_ask_dollars": "0.65",
    "no_ask_dollars": "0.40",
    "last_price_dollars": "0.63",
    "volume_24h_fp": "1234.5",
    "volume_fp": "98765.4",
    "open_interest_fp": "4321.0",
    "close_time": "2026-12-01T00:00:00Z",
}


def test_fetch_hurricane_markets_normalizes_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/markets")
        return httpx.Response(200, json={"markets": [_SAMPLE_RAW_MARKET]})

    client, _ = _make_client(handler)
    markets = fetch_hurricane_markets(["HURR-ATLANTIC"], client=client)

    assert len(markets) == 1
    m = markets[0]
    assert isinstance(m, KalshiMarket)
    assert m.platform == "Kalshi"
    assert m.series_ticker == "HURR-ATLANTIC"
    assert m.event_ticker == "HURR-ATLANTIC"
    assert m.ticker == "HURR-ATLANTIC-NUM-5PLUS"
    assert m.yes_bid == 0.62
    assert m.no_bid == 0.37
    assert m.volume_24h == 1234.5
    assert m.url == "https://kalshi.com/markets/HURR-ATLANTIC-NUM-5PLUS"


def test_fetch_hurricane_markets_handles_missing_fields() -> None:
    """Raw records with null/missing numeric fields should coerce to 0.0."""

    sparse_raw = {"ticker": "X", "event_ticker": "Y", "title": "Sparse market"}

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"markets": [sparse_raw]})

    client, _ = _make_client(handler)
    markets = fetch_hurricane_markets(["SERIES"], client=client)

    assert len(markets) == 1
    m = markets[0]
    assert m.yes_bid == 0.0
    assert m.volume_24h == 0.0
    assert m.url == "https://kalshi.com/markets/X"


def test_fetch_hurricane_markets_continues_past_per_series_failure() -> None:
    """One failing series shouldn't poison the batch — log and move on."""

    def handler(request: httpx.Request) -> httpx.Response:
        series = request.url.params.get("series_ticker")
        if series == "BAD":
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"markets": [_SAMPLE_RAW_MARKET]})

    client, _ = _make_client(handler)
    markets = fetch_hurricane_markets(["GOOD", "BAD", "ALSO-GOOD"], client=client)

    assert [m.series_ticker for m in markets] == ["GOOD", "ALSO-GOOD"]


def test_fetch_hurricane_markets_returns_empty_for_empty_input() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no series → no HTTP calls expected")

    client, _ = _make_client(handler)
    assert fetch_hurricane_markets([], client=client) == []


# ----- client_from_settings + load_private_key ----------------------------


def test_client_from_settings_raises_when_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from rmn_dashboard import config as config_module

    monkeypatch.setattr(config_module.settings, "kalshi_api_key_id", None, raising=False)
    monkeypatch.setattr(
        config_module.settings, "kalshi_private_key_path", "/tmp/unused.key", raising=False
    )

    with pytest.raises(KalshiConfigError, match="KALSHI_API_KEY_ID"):
        client_from_settings()


def test_client_from_settings_raises_when_key_path_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rmn_dashboard import config as config_module

    monkeypatch.setattr(config_module.settings, "kalshi_api_key_id", "some-id", raising=False)
    monkeypatch.setattr(config_module.settings, "kalshi_private_key_path", None, raising=False)

    with pytest.raises(KalshiConfigError, match="KALSHI_PRIVATE_KEY_PATH"):
        client_from_settings()


def test_load_private_key_raises_for_missing_file(tmp_path: Any) -> None:
    missing = tmp_path / "nope.pem"
    with pytest.raises(KalshiConfigError, match="not found"):
        load_private_key(missing)
