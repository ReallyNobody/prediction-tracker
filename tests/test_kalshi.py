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
    sleep_fn: Callable[[float], None] | None = None,
    max_429_retries: int = 4,
) -> tuple[KalshiClient, StubSigner]:
    """Construct a KalshiClient wired to an in-process MockTransport.

    ``sleep_fn`` defaults to a no-op so tests that don't care about backoff
    pacing never actually sleep. Tests that want to assert the sleep schedule
    should pass their own recorder.
    """
    signer = signer or StubSigner()
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    client = KalshiClient(
        api_key_id=api_key_id,
        private_key=signer,
        base_url=base_url,
        http_client=http,
        max_429_retries=max_429_retries,
        sleep_fn=sleep_fn or (lambda _seconds: None),
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
    # per_series_sleep=0 so this test doesn't pay for real wall time.
    markets = fetch_hurricane_markets(
        ["GOOD", "BAD", "ALSO-GOOD"], client=client, per_series_sleep=0
    )

    assert [m.series_ticker for m in markets] == ["GOOD", "ALSO-GOOD"]


def test_fetch_hurricane_markets_returns_empty_for_empty_input() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no series → no HTTP calls expected")

    client, _ = _make_client(handler)
    assert fetch_hurricane_markets([], client=client) == []


def test_fetch_hurricane_markets_paces_sleep_between_series() -> None:
    """Sleep once before each series after the first — not before the first,
    and not after the last. Three series → two pacing sleeps."""
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"markets": []})

    client, _ = _make_client(handler)
    fetch_hurricane_markets(
        ["A", "B", "C"],
        client=client,
        sleep_fn=sleeps.append,
        per_series_sleep=0.5,
    )
    assert sleeps == [0.5, 0.5]


# ----- KalshiClient 429 retry ---------------------------------------------


def test_client_retries_on_429_then_succeeds() -> None:
    """One 429 followed by a 200 should transparently succeed after a single
    backoff sleep of 1s (2**0)."""
    sleeps: list[float] = []
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": "too_many_requests"})
        return httpx.Response(200, json={"markets": []})

    client, _ = _make_client(handler, sleep_fn=sleeps.append)
    result = client.get("/markets")

    assert result == {"markets": []}
    assert len(calls) == 2  # one 429 + one 200
    assert sleeps == [1]  # 2**0 on the first retry


def test_client_429_retry_exhausts_and_raises() -> None:
    """When every attempt returns 429, the client exhausts retries and raises
    HTTPStatusError. Sleep schedule is 1, 2, 4, 8 for the 4 default retries."""
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "too_many_requests"})

    client, _ = _make_client(handler, sleep_fn=sleeps.append, max_429_retries=4)
    with pytest.raises(httpx.HTTPStatusError):
        client.get("/markets")

    # One sleep per retry attempt; the final attempt does not sleep before raising.
    assert sleeps == [1, 2, 4, 8]


def test_client_honors_retry_after_header_on_429() -> None:
    """Day 42: when Kalshi sends a Retry-After header on 429, the
    client should use that value for the next backoff instead of the
    default 2**attempt. Authoritative beats heuristic."""
    sleeps: list[float] = []
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            # Kalshi tells us "wait 5 seconds" — should override the
            # 2**0 = 1s default for this attempt.
            return httpx.Response(
                429,
                json={"error": "too_many_requests"},
                headers={"Retry-After": "5"},
            )
        return httpx.Response(200, json={"markets": []})

    client, _ = _make_client(handler, sleep_fn=sleeps.append)
    result = client.get("/markets")

    assert result == {"markets": []}
    assert sleeps == [5.0]


def test_client_caps_backoff_at_max_seconds() -> None:
    """Day 42: with the higher MAX_429_RETRIES=6 default, attempt 5
    would naively want 2**5 = 32s of backoff. The MAX_BACKOFF_SECONDS
    cap (30s) keeps the worst-case wait bounded."""
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "too_many_requests"})

    client, _ = _make_client(handler, sleep_fn=sleeps.append, max_429_retries=6)
    with pytest.raises(httpx.HTTPStatusError):
        client.get("/markets")

    # Sequence: 1, 2, 4, 8, 16, then 32 capped to 30.
    assert sleeps == [1, 2, 4, 8, 16, 30]


def test_client_caps_retry_after_header_at_max_seconds() -> None:
    """Day 42: a malicious or misconfigured Retry-After of 600s
    shouldn't park our scheduler thread for ten minutes. The cap
    applies to header-supplied values too."""
    sleeps: list[float] = []
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(
                429,
                json={"error": "too_many_requests"},
                headers={"Retry-After": "600"},
            )
        return httpx.Response(200, json={"markets": []})

    client, _ = _make_client(handler, sleep_fn=sleeps.append)
    client.get("/markets")
    assert sleeps == [30.0]


def test_client_does_not_retry_on_non_429_error() -> None:
    """A 500 (or any non-429 error) must raise on the first attempt — no
    backoff, no extra calls."""
    sleeps: list[float] = []
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, json={"error": "boom"})

    client, _ = _make_client(handler, sleep_fn=sleeps.append)
    with pytest.raises(httpx.HTTPStatusError):
        client.get("/markets")

    assert len(calls) == 1
    assert sleeps == []


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
