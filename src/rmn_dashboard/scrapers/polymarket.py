"""Polymarket Gamma API client — fetches hurricane-related markets.

Polymarket's Gamma API (https://gamma-api.polymarket.com) is public and
unauthenticated, which keeps the auth surface much simpler than Kalshi's
RSA-PSS-signed requests.

This module exposes a sync ``PolymarketClient`` (thin wrapper around
``httpx``) and a ``fetch_hurricane_markets()`` entry point that pulls
hurricane-tagged events from ``/events?tag_slug=hurricane`` and
flattens their nested ``markets`` array into normalized
``PolymarketMarket`` records.

Endpoint history (audit-driven, June 2026):

  Day 37 (May 2026) shipped against ``/markets?closed=false`` with a
  client-side keyword regex over the title. That approach silently
  stopped finding hurricane markets in mid-May once Polymarket grew
  past our pagination ceiling (25 pages × 200 = 5000 markets) and/or
  restructured how the flat /markets catalog surfaces tagged questions.
  The June 16 audit caught a 33-day data gap — Polymarket snapshots
  were last updated 2026-05-14.

  This rewrite hits ``/events?tag_slug=hurricane`` instead. Polymarket
  publishes a curated hurricane tag (id 102023, slug "hurricane")
  which is the same source they use for polymarket.com/markets/hurricane.
  Targeted query, no pagination ceiling, no client-side regex needed
  — their own editorial categorization is the source of truth.

Response-shape note:

  ``/events?tag_slug=hurricane`` returns event objects, each with a
  nested ``markets`` array. ``_flatten_event_markets`` extracts each
  child market into the flat shape ``_normalize_market`` already
  consumes, re-attaching the parent event as ``events: [parent]`` so
  ``openInterest`` and ``event_ticker`` keep working unchanged.

Architectural choices, deliberately mirroring ``scrapers/kalshi.py``:

  * Sync + httpx (not async) — matches the rest of the codebase.
  * Injectable ``httpx.Client`` so tests use ``httpx.MockTransport``
    and never touch the network.
  * Frozen dataclass record so downstream code (the ingest task) sees
    a stable, typed shape regardless of upstream API changes.
  * Exponential backoff on 429.

What this module does NOT do:

  * Authentication. Polymarket Gamma is public; no key, no signing.
  * Trade execution. We're a read-only consumer of the public market
    catalog, not a trading client.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# Polymarket's curated hurricane tag — the same slug they use for the
# polymarket.com/markets/hurricane category page. Querying
# /events?tag_slug=hurricane returns exactly the editorially-tagged
# subset, no client-side filtering needed.
_HURRICANE_TAG_SLUG = "hurricane"

# How many hurricane-tagged events to pull per request. 100 is
# generously above the live hurricane catalog (~5-10 events in
# steady state) and well inside any per-request limit. The endpoint
# is targeted enough that pagination is effectively unnecessary; we
# request once and trust the response to contain everything.
EVENT_FETCH_LIMIT = 100

MAX_429_RETRIES = 4
HTTP_TIMEOUT = 30.0


@dataclass(frozen=True)
class PolymarketMarket:
    """Normalized Polymarket market record — one row for Panel 4.

    Mirrors the shape of ``KalshiMarket`` so the ingest task can map
    both platforms onto the same ``PredictionMarket`` rows without
    branching on type.
    """

    platform: str  # always "polymarket"
    ticker: str  # the slug — used as URL key (polymarket.com/event/{ticker})
    event_ticker: str | None  # parent event slug if available; else None
    title: str
    yes_price: float | None  # 0.0–1.0 (matches Kalshi convention; UI multiplies *100)
    no_price: float | None  # 0.0–1.0
    volume_24h: float | None  # USDC traded in the last 24h
    volume_total: float | None  # cumulative USDC traded
    open_interest: float | None  # USDC of contracts currently outstanding
    close_time: str | None  # ISO-8601 string; ingest task parses to date
    url: str  # absolute polymarket.com URL


class PolymarketClient:
    """Tiny synchronous HTTPx wrapper for the Gamma API.

    Public so tests can construct one with a ``MockTransport`` and
    inject it into ``fetch_hurricane_markets``. The default constructor
    builds a real client pointed at ``settings.polymarket_base_url``.
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET path with exponential backoff on 429. Returns parsed JSON."""
        for attempt in range(MAX_429_RETRIES + 1):
            try:
                response = self._client.get(path, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and attempt < MAX_429_RETRIES:
                    wait = 2**attempt
                    logger.warning(
                        "Polymarket 429 on %s; backing off %ds (attempt %d/%d)",
                        path,
                        wait,
                        attempt + 1,
                        MAX_429_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                raise
        raise RuntimeError("unreachable")  # pragma: no cover

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PolymarketClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def client_from_settings() -> PolymarketClient:
    """Build a real ``PolymarketClient`` against the configured base URL.

    Uses a courtesy User-Agent so Polymarket can identify us in their
    logs if our usage misbehaves — same convention as the SEC scraper
    User-Agent we use for NHC.
    """
    # Lazy import so importing this module doesn't transitively pull
    # config + pydantic into tests that pass an explicit client.
    from rmn_dashboard.config import settings

    return PolymarketClient(
        httpx.Client(
            base_url=settings.polymarket_base_url,
            timeout=HTTP_TIMEOUT,
            headers={
                "User-Agent": (f"Risk Market News dashboard ({settings.sec_user_agent})"),
                "Accept": "application/json",
            },
        )
    )


# ----- Parsing helpers ----------------------------------------------------


def _parse_outcome_prices(raw: str | None) -> tuple[float | None, float | None]:
    """Polymarket encodes outcomes + prices as JSON-strings.

    Example: ``'["0.0195", "0.9805"]'`` → (0.0195, 0.9805). The first
    element is "Yes" (per ``outcomes``), the second is "No". Hurricane
    markets in our universe are all binary Yes/No, so we just pick the
    first two elements; if the encoding ever changes (multi-outcome
    markets) we'll see None/None and the caller will skip the market.
    """
    if not raw:
        return None, None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("Polymarket outcomePrices unparseable: %r", raw)
        return None, None
    if not isinstance(parsed, list) or len(parsed) < 2:
        return None, None
    try:
        yes = float(parsed[0])
        no = float(parsed[1])
    except (TypeError, ValueError):
        logger.warning("Polymarket outcomePrices not numeric: %r", parsed)
        return None, None
    return yes, no


def _open_interest_from_events(events_field: Any) -> float | None:
    """Polymarket reports OI at the parent-event level, not per-market.

    Each market's ``events`` is a list (typically length 1 per the
    probe data) with an ``openInterest`` float. We pull from the first
    event since every probed hurricane market had one. If the shape
    drifts, return None and skip the field rather than crash the batch.
    """
    if not isinstance(events_field, list) or not events_field:
        return None
    first = events_field[0]
    if not isinstance(first, dict):
        return None
    raw = first.get("openInterest")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _event_ticker_from_events(events_field: Any) -> str | None:
    """Pull the parent event slug if available — used as ``event_ticker``."""
    if not isinstance(events_field, list) or not events_field:
        return None
    first = events_field[0]
    if not isinstance(first, dict):
        return None
    slug = first.get("slug") or first.get("ticker")
    return str(slug) if slug else None


def _to_polymarket_url(slug: str) -> str:
    """Polymarket's canonical event URL pattern.

    Verified live against polymarket.com on Day 36 — the /event/{slug}
    path resolves cleanly for every hurricane market the probe matched.
    /market/{slug} also works on the Polymarket side but redirects to
    /event/, so we use /event/ to skip the redirect.
    """
    return f"https://polymarket.com/event/{slug}"


def _normalize_market(raw: dict[str, Any]) -> PolymarketMarket | None:
    """Convert one raw API dict into our typed ``PolymarketMarket`` record.

    Returns None if required fields are missing or unparseable; the
    caller drops Nones from the batch rather than aborting.
    """
    slug = raw.get("slug")
    title = raw.get("question") or raw.get("title")
    if not slug or not title:
        return None

    yes_price, no_price = _parse_outcome_prices(raw.get("outcomePrices"))

    # `volumeNum` and `volume24hr` are pre-parsed floats per probe data.
    # Cast defensively in case the API ships strings on some endpoints.
    volume_total = _coerce_float(raw.get("volumeNum"))
    volume_24h = _coerce_float(raw.get("volume24hr"))
    open_interest = _open_interest_from_events(raw.get("events"))

    return PolymarketMarket(
        platform="polymarket",
        ticker=str(slug),
        event_ticker=_event_ticker_from_events(raw.get("events")),
        title=str(title),
        yes_price=yes_price,
        no_price=no_price,
        volume_24h=volume_24h,
        volume_total=volume_total,
        open_interest=open_interest,
        close_time=raw.get("endDate") or raw.get("endDateIso"),
        url=_to_polymarket_url(str(slug)),
    )


def _coerce_float(value: Any) -> float | None:
    """Best-effort float() that returns None on missing or unparseable input."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ----- Event fetch + flatten --------------------------------------------


def _fetch_hurricane_events(client: PolymarketClient) -> list[dict[str, Any]]:
    """Fetch all open hurricane-tagged events from Polymarket.

    Hits ``/events?tag_slug=hurricane&closed=false&archived=false``.
    Returns the parsed list (or empty on network error / unexpected
    response shape) — never raises. The endpoint is targeted enough
    that no pagination is needed at hurricane volumes.
    """
    try:
        payload = client.get(
            "/events",
            params={
                "tag_slug": _HURRICANE_TAG_SLUG,
                "closed": "false",
                "archived": "false",
                "limit": EVENT_FETCH_LIMIT,
            },
        )
    except httpx.HTTPError as exc:
        logger.warning("Polymarket /events?tag_slug=hurricane failed: %s", exc)
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("events"), list):
        return payload["events"]
    logger.warning("Unexpected /events payload shape: %s", type(payload).__name__)
    return []


def _flatten_event_markets(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pull each event's ``markets`` array out into a flat list.

    The inner market dicts already match the shape ``_normalize_market``
    consumes (slug, question, outcomes, outcomePrices, volumeNum, etc.).
    We re-attach the parent event as ``events: [parent]`` on each child
    so ``_open_interest_from_events`` and ``_event_ticker_from_events``
    keep working without modification — they read the parent the same
    way the old /markets endpoint reported it.

    Filters out markets that aren't currently tradeable
    (``active=false`` or ``closed=true``). Polymarket sometimes leaves
    paused / draft markets nested under an active event; we don't want
    them in the heat-map.
    """
    out: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        nested = event.get("markets")
        if not isinstance(nested, list):
            continue
        # Build a slim parent record that preserves the fields our
        # ``events[0]`` readers care about — slug, ticker, openInterest.
        # Copying the whole event would balloon the in-memory size and
        # carry irrelevant fields downstream.
        parent = {
            "slug": event.get("slug") or event.get("ticker"),
            "ticker": event.get("ticker") or event.get("slug"),
            "openInterest": event.get("openInterest"),
        }
        for market in nested:
            if not isinstance(market, dict):
                continue
            if market.get("closed") is True:
                continue
            if market.get("active") is False:
                continue
            # Inject parent so the normalizer's ``events[0]`` reads work.
            market_with_parent = dict(market)
            market_with_parent["events"] = [parent]
            out.append(market_with_parent)
    return out


# ----- Public entry point ------------------------------------------------


def fetch_hurricane_markets(*, client: PolymarketClient | None = None) -> list[PolymarketMarket]:
    """Fetch all open Polymarket hurricane markets.

    Args:
        client: optional client for tests / dependency injection.
            Production callers (the scheduler) leave None; we build a
            fresh client per call from settings.

    Returns:
        Normalized list. Empty list on error or no matches — never raises
        to the caller. Mirrors the Kalshi entry point's contract so the
        scheduler's job wrapper can handle both with the same try/except
        shape.
    """
    owned_client = client is None
    if client is None:
        client = client_from_settings()

    try:
        events = _fetch_hurricane_events(client)
    finally:
        if owned_client:
            client.close()

    matches = _flatten_event_markets(events)
    logger.info(
        "Polymarket: %d hurricane-tagged events, %d tradeable markets.",
        len(events),
        len(matches),
    )

    normalized: list[PolymarketMarket] = []
    for raw in matches:
        record = _normalize_market(raw)
        if record is not None:
            normalized.append(record)
    if len(normalized) != len(matches):
        logger.warning(
            "Polymarket: %d hits but %d normalized (some unparseable).",
            len(matches),
            len(normalized),
        )
    return normalized


# Public alias matching the Kalshi module's exported set so the task
# layer can import a parallel name. Polymarket's editorial categorization
# (the "hurricane" tag) is the equivalent of Kalshi's series ticker —
# it identifies the subset of the catalog we care about, with no
# client-side keyword work needed.
HURRICANE_TAG_SLUG = _HURRICANE_TAG_SLUG


__all__: Iterable[str] = (
    "HURRICANE_TAG_SLUG",
    "PolymarketClient",
    "PolymarketMarket",
    "client_from_settings",
    "fetch_hurricane_markets",
)
