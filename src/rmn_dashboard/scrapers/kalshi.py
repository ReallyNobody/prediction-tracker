"""Kalshi authenticated trade-api client — fetches hurricane-related markets.

Kalshi's private-key auth scheme signs each request with RSA-PSS over
``{timestamp_ms}{METHOD}{path_without_query}`` using SHA-256. Three custom
headers ride on every request:

    KALSHI-ACCESS-KEY        — the public API key ID
    KALSHI-ACCESS-SIGNATURE  — base64(RSA-PSS(SHA256, key, message))
    KALSHI-ACCESS-TIMESTAMP  — current Unix time in milliseconds, as a string

This module exposes a sync ``KalshiClient`` (wraps ``httpx``) and a
``fetch_hurricane_markets(series_tickers)`` entry point that batches a list
of series tickers into normalized ``KalshiMarket`` records for the
"Markets on it" panel. Scheduling lives elsewhere (Week 2, APScheduler).

Why sync + httpx, not requests + async: httpx gives us a consistent client
across every scraper in this package, and sync matches the rest of the
codebase (see architecture-decisions.md). When we eventually parallelize
scrapers, we'll do it with a thread pool, not asyncio.

Testing notes: the module accepts an injectable ``httpx.Client``, so tests
wire an ``httpx.MockTransport`` and never touch the network. The
``KalshiClient`` accepts any object with a ``.sign(...)`` method of the
right shape, so tests don't need real RSA keys either — a tiny stub is
enough. See ``tests/test_kalshi.py``.
"""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from rmn_dashboard.config import settings

logger = logging.getLogger(__name__)


# Kalshi series tickers that host hurricane/tropical-storm markets.
# Discovered 2026-04-23 via ``scripts/probe_kalshi.py`` against /events?status=open.
# Re-run the probe periodically (especially during Atlantic season, Jun–Nov) to
# catch new series Kalshi spins up for landfall, ACE index, regional markets,
# etc. Add verified tickers here as they appear.
HURRICANE_SERIES: tuple[str, ...] = (
    "KXHURCTOT",  # Atlantic hurricane count (total, full season)
    "KXHURCTOTMAJ",  # Major Atlantic hurricane count (Cat 3+)
    "KXTROPSTORM",  # Tropical storm count (named storms)
)

# Rate-limit tunables — Kalshi will 429 on bursts of a few back-to-back calls.
#
# Day 42: bumped after prod 429 storms where the previous 1s/2s/4s/8s
# backoff repeatedly failed all four attempts. Kalshi's rate-limit
# window is clearly wider than 8 seconds. Two changes:
#
#   1. PER_SERIES_SLEEP: 0.5s → 2.0s. Inter-series pacing now sits
#      well above whatever burst threshold Kalshi enforces.
#   2. MAX_429_RETRIES: 4 → 6, with a per-attempt cap of MAX_BACKOFF_SECONDS.
#      Effective sequence: 1, 2, 4, 8, 16, 30s — up to ~61s of total
#      patience per call, which lets the rate-limit window roll over
#      cleanly instead of churning through guaranteed-fail retries.
#   3. Retry-After honored when present. Kalshi may not send it, but
#      if they do, it's authoritative and we should respect it.
PER_SERIES_SLEEP = 2.0
MAX_429_RETRIES = 6
MAX_BACKOFF_SECONDS = 30.0


class KalshiConfigError(RuntimeError):
    """Raised when Kalshi credentials are missing, unreadable, or wrong type."""


class _Signer(Protocol):
    """Minimal surface we need from an RSA private key — lets tests use a stub."""

    def sign(self, data: bytes, pad: Any, algo: Any) -> bytes: ...


@dataclass(frozen=True)
class KalshiMarket:
    """Normalized Kalshi market record — one row for the 'Markets on it' panel."""

    platform: str
    series_ticker: str
    event_ticker: str | None
    ticker: str | None
    title: str | None
    subtitle: str | None
    yes_bid: float
    no_bid: float
    yes_ask: float
    no_ask: float
    last_price: float
    volume_24h: float
    volume_total: float
    open_interest: float
    close_time: str | None
    url: str


def load_private_key(key_path: str | Path) -> _Signer:
    """Load an RSA private key from a PEM file on disk.

    Raises ``KalshiConfigError`` on missing file or non-RSA key material,
    so callers get a single, well-typed exception to handle.
    """
    path = Path(key_path)
    try:
        pem = path.read_bytes()
    except FileNotFoundError as exc:
        raise KalshiConfigError(f"Kalshi private key not found at {path}") from exc

    key = serialization.load_pem_private_key(pem, password=None)
    if not hasattr(key, "sign"):
        raise KalshiConfigError(f"Loaded key at {path} is not a signing-capable private key.")
    return key  # type: ignore[return-value]


def _sign_request(signer: _Signer, timestamp_ms: str, method: str, path: str) -> str:
    """Build the base64 RSA-PSS signature Kalshi expects for a request.

    ``path`` must include everything from the URL after the host (e.g.
    ``/trade-api/v2/markets``), with any query string stripped — Kalshi
    signs only the path, not the query.
    """
    path_without_query = path.split("?", 1)[0]
    message = f"{timestamp_ms}{method.upper()}{path_without_query}".encode()

    signature_bytes = signer.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature_bytes).decode("ascii")


class KalshiClient:
    """Synchronous Kalshi trade-api client.

    Construct with an API key ID, a pre-loaded signing key (real RSA in prod,
    a stub in tests), and optionally your own ``httpx.Client``. Supports use
    as a context manager — owns the HTTP client only if it created it.
    """

    def __init__(
        self,
        api_key_id: str,
        private_key: _Signer,
        base_url: str | None = None,
        http_client: httpx.Client | None = None,
        max_429_retries: int = MAX_429_RETRIES,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key_id:
            raise KalshiConfigError("Missing Kalshi API key ID.")
        self._api_key_id = api_key_id
        self._private_key = private_key
        self._base_url = (base_url or settings.kalshi_base_url).rstrip("/")
        self._http_client = http_client or httpx.Client(timeout=30.0)
        self._owns_http_client = http_client is None
        self._max_429_retries = max_429_retries
        self._sleep = sleep_fn

    def __enter__(self) -> KalshiClient:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()

    def _build_headers(self, method: str, signed_path: str) -> dict[str, str]:
        timestamp_ms = str(int(time.time() * 1000))
        signature = _sign_request(self._private_key, timestamp_ms, method, signed_path)
        return {
            "KALSHI-ACCESS-KEY": self._api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        }

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Authenticated GET with 429 exponential backoff. Returns parsed JSON.

        On HTTP 429 we back off ``2**attempt`` seconds (1, 2, 4, 8…) and
        re-sign the request — the timestamp has to be fresh each attempt or
        Kalshi will reject the retry. Any non-429 error surfaces immediately.
        Raises ``httpx.HTTPError`` subclasses on transport or status issues —
        callers decide whether to log-and-skip or propagate.
        """
        if not path.startswith("/"):
            path = "/" + path

        url = self._base_url + path
        signed_path = urlparse(url).path  # e.g. /trade-api/v2/markets

        for attempt in range(self._max_429_retries + 1):
            # Re-sign on every attempt: Kalshi rejects stale timestamps.
            headers = self._build_headers("GET", signed_path)
            response = self._http_client.get(url, params=params, headers=headers)

            if response.status_code == 429 and attempt < self._max_429_retries:
                # Day 42: prefer Retry-After if Kalshi provides it (it's
                # authoritative — they know exactly when their rate-limit
                # window resets). Otherwise fall back to capped exponential
                # backoff. The cap (MAX_BACKOFF_SECONDS) keeps the worst-
                # case wait bounded; the higher MAX_429_RETRIES keeps the
                # total retry budget high enough to ride out windows that
                # are wider than 8 seconds.
                retry_after_header = response.headers.get("Retry-After")
                wait: float
                if retry_after_header is not None:
                    try:
                        wait = min(float(retry_after_header), MAX_BACKOFF_SECONDS)
                    except ValueError:
                        wait = min(2.0**attempt, MAX_BACKOFF_SECONDS)
                else:
                    wait = min(2.0**attempt, MAX_BACKOFF_SECONDS)
                logger.warning(
                    "Kalshi GET %s → 429; backing off %.1fs (attempt %d/%d)",
                    path,
                    wait,
                    attempt + 1,
                    self._max_429_retries,
                )
                self._sleep(wait)
                continue

            if response.is_error:
                # Surface Kalshi's body on non-2xx — it almost always contains
                # the actual cause (e.g. "invalid status filter 'active'"),
                # which httpx's default HTTPStatusError hides. Logged at
                # WARNING so it shows up in ordinary scraper runs, not only
                # when debug is on.
                body = response.text[:500]  # cap in case of HTML error pages
                logger.warning("Kalshi GET %s → %s: %s", path, response.status_code, body)
            response.raise_for_status()
            return response.json()

        raise RuntimeError("unreachable")  # pragma: no cover


def client_from_settings(http_client: httpx.Client | None = None) -> KalshiClient:
    """Build a ``KalshiClient`` from app settings.

    Raises ``KalshiConfigError`` when either the API key ID or private key
    path isn't configured, so the failure is explicit and actionable.
    """
    if not settings.kalshi_api_key_id:
        raise KalshiConfigError(
            "KALSHI_API_KEY_ID is not set. Configure it via environment or .env."
        )
    if not settings.kalshi_private_key_path:
        raise KalshiConfigError(
            "KALSHI_PRIVATE_KEY_PATH is not set. Configure it via environment or .env."
        )

    private_key = load_private_key(settings.kalshi_private_key_path)
    return KalshiClient(
        api_key_id=settings.kalshi_api_key_id,
        private_key=private_key,
        base_url=settings.kalshi_base_url,
        http_client=http_client,
    )


def _as_float(value: Any) -> float:
    """Best-effort float coercion. Missing/None/empty string → 0.0."""
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_market(raw: dict[str, Any], series_ticker: str) -> KalshiMarket:
    ticker = raw.get("ticker")
    return KalshiMarket(
        platform="Kalshi",
        series_ticker=series_ticker,
        event_ticker=raw.get("event_ticker"),
        ticker=ticker,
        title=raw.get("title"),
        subtitle=raw.get("subtitle"),
        yes_bid=_as_float(raw.get("yes_bid_dollars")),
        no_bid=_as_float(raw.get("no_bid_dollars")),
        yes_ask=_as_float(raw.get("yes_ask_dollars")),
        no_ask=_as_float(raw.get("no_ask_dollars")),
        last_price=_as_float(raw.get("last_price_dollars")),
        volume_24h=_as_float(raw.get("volume_24h_fp")),
        volume_total=_as_float(raw.get("volume_fp")),
        open_interest=_as_float(raw.get("open_interest_fp")),
        close_time=raw.get("close_time"),
        url=f"https://kalshi.com/markets/{ticker}" if ticker else "",
    )


def fetch_hurricane_markets(
    series_tickers: Iterable[str],
    client: KalshiClient | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    per_series_sleep: float = PER_SERIES_SLEEP,
) -> list[KalshiMarket]:
    """Fetch active markets across the given series tickers, normalized.

    Iterates the series sequentially and paces the calls with a short sleep
    between series — Kalshi 429s on even 2–3 back-to-back ``/markets`` calls.
    The client itself retries inside a single call with exponential backoff,
    so this pacing is the pre-emptive belt-and-suspenders layer.

    A per-series failure is logged and skipped — the caller gets whatever
    succeeded. If ``client`` is omitted, one is built from settings and closed
    on exit; pass an explicit client (typically in tests, or to reuse a
    long-lived pool) to control lifecycle yourself.
    """
    owns_client = client is None
    if client is None:
        client = client_from_settings()

    try:
        markets: list[KalshiMarket] = []
        for index, series_ticker in enumerate(series_tickers):
            if index > 0 and per_series_sleep > 0:
                # Pre-emptive pacing: avoid tripping Kalshi's rate limiter on
                # the next call. Skipped for the first series (nothing to
                # pace against) and when tests inject per_series_sleep=0.
                sleep_fn(per_series_sleep)

            try:
                payload = client.get(
                    "/markets",
                    params={
                        "series_ticker": series_ticker,
                        # Kalshi v2 uses "open" (not "active" — the legacy
                        # scraper's value 400s now). Valid values: unopened,
                        # open, closed, settled.
                        "status": "open",
                        "limit": 100,
                    },
                )
            except httpx.HTTPError:
                logger.exception("Kalshi /markets fetch failed for series_ticker=%s", series_ticker)
                continue

            for raw in payload.get("markets", []):
                markets.append(_normalize_market(raw, series_ticker))

        return markets
    finally:
        if owns_client:
            client.close()
