"""NHC CurrentStorms.json scraper — fetches the active-storms array.

NHC publishes tropical-cyclone status at
``https://www.nhc.noaa.gov/CurrentStorms.json``. The feed is unauthenticated,
unmetered, and has no documented rate limit — we poll every 15 minutes and
have seen no 4xx in testing. No per-call retry/backoff layer is included
here; if NHC's CDN glitches, the next scheduled tick takes the next shot
(the scheduler's job wrapper catches the exception and logs it).

Schema reference: the authoritative JSON schema is documented in the NHC
Tropical Cyclone Status JSON File Reference (Greenlaw, 2019-04). Three
details are easy to get wrong and are worth flagging loudly:

  * ``movementSpeed`` is reported in MPH, NOT knots. ``intensity`` is in
    knots. Mixing the two silently produces plausible-looking nonsense.
  * Top level is ``{"activeStorms": [...]}``, not a bare array. Off-season
    ``activeStorms`` is an empty list — parse as success, not an error.
  * Advisory sub-objects (``forecastTrack``, ``trackCone``,
    ``publicAdvisory`` …) hold only ZIP/KMZ URL pointers, not inline
    forecast points. Consuming the forecast-track geometry requires
    parsing the shapefile zip — that's Day 10, not this module.

Testing notes: accepts an injectable ``httpx.Client`` (same pattern as the
Kalshi scraper), so tests wire an ``httpx.MockTransport`` and never touch
the network. See ``tests/test_nhc.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from rmn_dashboard.config import settings

logger = logging.getLogger(__name__)


# Known advisory sub-products published on each storm record. We store the
# full sub-object (advNum + issuance + url/zipFile/kmzFile) for each key
# present, so any new products NHC adds flow through automatically as long
# as we update this list. Keys not in this list are ignored — keeps the
# ``advisory_urls`` blob focused on storm products, not on top-level storm
# identity fields (name, classification, etc.).
_ADVISORY_URL_KEYS: tuple[str, ...] = (
    "publicAdvisory",
    "forecastAdvisory",
    "windSpeedProbabilities",
    "forecastDiscussion",
    "forecastGraphics",
    "forecastTrack",
    "windWatchesWarnings",
    "trackCone",
    "initialWindExtent",
    "forecastWindRadiiGIS",
    "bestTrackGIS",
    "earliestArrivalTimeTSWindsGIS",
    "mostLikelyTimeTSWindsGIS",
    "windSpeedProbabilitiesGIS",
    "stormSurgeWatchWarningGIS",
    "potentialStormSurgeFloodingGIS",
)


class NHCScrapeError(RuntimeError):
    """Raised when the NHC feed returns something we fundamentally can't parse."""


@dataclass(frozen=True)
class NHCStormObservation:
    """Normalized NHC active-storm observation — everything for one storm tick."""

    nhc_id: str
    bin_number: str | None
    name: str
    classification: str
    intensity_kt: int
    pressure_mb: int | None
    latitude_deg: float
    longitude_deg: float
    movement_dir_deg: int | None
    movement_speed_mph: int | None
    last_update: datetime
    advisory_urls: dict[str, Any]


def _parse_iso8601_utc(value: str) -> datetime:
    """Parse NHC's ``lastUpdate`` into a timezone-aware datetime.

    NHC publishes ISO8601 UTC strings — typically with a trailing ``Z``.
    ``datetime.fromisoformat`` didn't accept the ``Z`` suffix until Python
    3.11; we normalize to ``+00:00`` defensively so the same code works
    everywhere.
    """
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _coerce_int(raw: Any) -> int | None:
    """Best-effort int coercion. Empty / missing / unparseable → None."""
    if raw in (None, "", "NA"):
        return None
    try:
        # NHC publishes integers as bare numbers, but tolerate the string
        # form that shows up in the occasional hand-edited advisory.
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _coerce_required_int(raw: Any, field: str) -> int:
    value = _coerce_int(raw)
    if value is None:
        raise ValueError(f"Missing or unparseable required integer field: {field!r}")
    return value


def _coerce_required_float(raw: Any, field: str) -> float:
    if raw in (None, ""):
        raise ValueError(f"Missing required numeric field: {field!r}")
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Unparseable required numeric field {field!r}: {raw!r}") from exc


def _extract_advisory_urls(storm: dict[str, Any]) -> dict[str, Any]:
    """Pull the advisory sub-product blob from a storm record, whitelist-style.

    Each whitelisted key that's present on the storm is copied into the
    returned dict as-is (preserving ``advNum``, ``issuance``, and whichever
    of ``url`` / ``zipFile`` / ``kmzFile`` NHC provided). Missing keys are
    simply absent from the result.
    """
    return {key: storm[key] for key in _ADVISORY_URL_KEYS if key in storm}


def _normalize_storm(raw: dict[str, Any]) -> NHCStormObservation:
    """Convert one ``activeStorms`` element into our frozen dataclass.

    Required fields (id, name, classification, intensity, lat/lon, lastUpdate)
    raise ``ValueError`` when missing or unparseable — the caller logs and
    skips the storm, letting the rest of the batch through. Optional fields
    (pressure, movement) degrade to ``None``.
    """
    nhc_id = raw.get("id")
    if not nhc_id:
        raise ValueError("Storm record missing required 'id'")

    name = raw.get("name")
    if not name:
        raise ValueError(f"Storm {nhc_id} missing required 'name'")

    classification = raw.get("classification")
    if not classification:
        raise ValueError(f"Storm {nhc_id} missing required 'classification'")

    last_update_raw = raw.get("lastUpdate")
    if not last_update_raw:
        raise ValueError(f"Storm {nhc_id} missing required 'lastUpdate'")

    return NHCStormObservation(
        nhc_id=nhc_id,
        bin_number=raw.get("binNumber"),
        name=name,
        classification=classification,
        intensity_kt=_coerce_required_int(raw.get("intensity"), "intensity"),
        pressure_mb=_coerce_int(raw.get("pressure")),
        latitude_deg=_coerce_required_float(raw.get("latitude_numeric"), "latitude_numeric"),
        longitude_deg=_coerce_required_float(raw.get("longitude_numeric"), "longitude_numeric"),
        movement_dir_deg=_coerce_int(raw.get("movementDir")),
        movement_speed_mph=_coerce_int(raw.get("movementSpeed")),
        last_update=_parse_iso8601_utc(last_update_raw),
        advisory_urls=_extract_advisory_urls(raw),
    )


def _extract_active_storms(payload: Any) -> list[dict[str, Any]]:
    """Locate the activeStorms array inside the NHC payload.

    The documented shape is ``{"activeStorms": [...]}``; we accept a bare
    list as well so a future schema change that drops the wrapper doesn't
    take the whole pipeline offline. Anything else raises
    ``NHCScrapeError`` — unambiguous, caller-handled.
    """
    if isinstance(payload, dict):
        raw = payload.get("activeStorms")
        if raw is None:
            # Legitimate: some historical off-season payloads publish the
            # object without the key. Treat as empty.
            return []
        if not isinstance(raw, list):
            raise NHCScrapeError(f"'activeStorms' is not a list: {type(raw).__name__}")
        return raw
    if isinstance(payload, list):
        return payload
    raise NHCScrapeError(f"Unexpected top-level type from NHC: {type(payload).__name__}")


def fetch_active_storms(
    http_client: httpx.Client | None = None,
    url: str | None = None,
) -> list[NHCStormObservation]:
    """Fetch and normalize NHC's active-storms feed.

    A per-storm normalization failure is logged and skipped — the caller
    receives whichever storms parsed successfully. A transport failure or
    non-2xx status raises ``httpx.HTTPError`` so the scheduler's job wrapper
    can mark the whole tick as failed and retry next interval.

    If ``http_client`` is omitted, a short-lived client is built with a
    sensible timeout and a courtesy User-Agent; callers that want to reuse
    a pool or mock the transport should pass one in.
    """
    feed_url = url or settings.nhc_current_storms_url

    owns_client = http_client is None
    client = http_client or httpx.Client(
        timeout=30.0,
        # NHC doesn't require this, but sending something descriptive is
        # good hygiene (and mirrors the SEC EDGAR convention).
        headers={"User-Agent": settings.sec_user_agent},
    )

    try:
        response = client.get(feed_url)
        if response.is_error:
            body = response.text[:500]
            logger.warning("NHC GET %s → %s: %s", feed_url, response.status_code, body)
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns_client:
            client.close()

    raw_storms = _extract_active_storms(payload)

    observations: list[NHCStormObservation] = []
    for raw in raw_storms:
        if not isinstance(raw, dict):
            logger.warning("NHC: skipping non-object storm entry: %r", raw)
            continue
        try:
            observations.append(_normalize_storm(raw))
        except ValueError as exc:
            logger.warning(
                "NHC: skipping malformed storm record (%s): %r",
                exc,
                {k: raw.get(k) for k in ("id", "name", "classification")},
            )

    return observations
