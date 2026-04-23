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
from collections.abc import Iterable
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
    ) -> None:
        if not api_key_id:
            raise KalshiConfigError("Missing Kalshi API key ID.")
        self._api_key_id = api_key_id
        self._private_key = private_key
        self._base_url = (base_url or settings.kalshi_base_url).rstrip("/")
        self._http_client = http_client or httpx.Client(timeout=30.0)
        self._owns_http_client = http_client is None

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
        """Authenticated GET. Returns the parsed JSON body.

        Raises ``httpx.HTTPError`` subclasses on transport or status issues —
        callers decide whether to log-and-skip or propagate.
        """
        if not path.startswith("/"):
            path = "/" + path

        url = self._base_url + path
        signed_path = urlparse(url).path  # e.g. /trade-api/v2/markets
        headers = self._build_headers("GET", signed_path)

        response = self._http_client.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.json()


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
) -> list[KalshiMarket]:
    """Fetch active markets across the given series tickers, normalized.

    Iterates the series sequentially (Kalshi rate-limits are generous but not
    infinite). A per-series failure is logged and skipped — the caller gets
    whatever succeeded. If ``client`` is omitted, one is built from settings
    and closed on exit; pass an explicit client (typically in tests, or to
    reuse a long-lived pool) to control lifecycle yourself.
    """
    owns_client = client is None
    if client is None:
        client = client_from_settings()

    try:
        markets: list[KalshiMarket] = []
        for series_ticker in series_tickers:
            try:
                payload = client.get(
                    "/markets",
                    params={
                        "series_ticker": series_ticker,
                        "status": "active",
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
