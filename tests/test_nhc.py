"""Unit tests for the NHC CurrentStorms.json scraper.

Never hits the network: ``httpx`` requests flow through ``MockTransport``.
The sample payload under ``tests/fixtures/nhc_current_storms.json`` is
modelled on the NHC schema reference (Greenlaw, 2019-04) — two storms
(Irma + Jose, 2017-09-09) with the sub-object shape we expect in
production. Using real historical values keeps test data realistic while
staying out of any copyright-sensitive domain.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from rmn_dashboard.scrapers.nhc import (
    NHCScrapeError,
    NHCStormObservation,
    _coerce_int,
    _coerce_required_float,
    _coerce_required_int,
    _extract_active_storms,
    _extract_advisory_urls,
    _normalize_storm,
    _parse_iso8601_utc,
    fetch_active_storms,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "nhc_current_storms.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def _make_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ----- Pure helpers --------------------------------------------------------


def test_parse_iso8601_utc_handles_z_suffix() -> None:
    dt = _parse_iso8601_utc("2017-09-09T15:00:00.000Z")
    assert dt == datetime(2017, 9, 9, 15, 0, tzinfo=UTC)


def test_parse_iso8601_utc_handles_explicit_offset() -> None:
    dt = _parse_iso8601_utc("2017-09-09T15:00:00+00:00")
    assert dt == datetime(2017, 9, 9, 15, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (15, 15),
        ("15", 15),
        (15.4, 15),  # floor-like: NHC sometimes publishes numeric strings that round
        (None, None),
        ("", None),
        ("NA", None),
        ("not a number", None),
    ],
)
def test_coerce_int_variants(raw: object, expected: int | None) -> None:
    assert _coerce_int(raw) == expected


def test_coerce_required_int_raises_on_missing() -> None:
    with pytest.raises(ValueError, match="intensity"):
        _coerce_required_int(None, "intensity")


def test_coerce_required_float_raises_on_unparseable() -> None:
    with pytest.raises(ValueError, match="latitude_numeric"):
        _coerce_required_float("WAT", "latitude_numeric")


def test_extract_advisory_urls_whitelists_known_keys() -> None:
    storm = {
        "id": "al112017",
        "name": "Irma",
        # Whitelisted keys:
        "publicAdvisory": {"advNum": "38", "url": "..."},
        "forecastTrack": {"advNum": "38", "zipFile": "..."},
        # Non-whitelisted keys (top-level identity fields, etc.):
        "intensity": "160",
        "classification": "HU",
        "someRandomKey": {"not": "an advisory product"},
    }
    urls = _extract_advisory_urls(storm)
    assert set(urls.keys()) == {"publicAdvisory", "forecastTrack"}
    assert urls["publicAdvisory"]["advNum"] == "38"


# ----- _normalize_storm ----------------------------------------------------


def test_normalize_storm_happy_path() -> None:
    fixture = _load_fixture()
    raw_irma = fixture["activeStorms"][0]

    obs = _normalize_storm(raw_irma)

    assert isinstance(obs, NHCStormObservation)
    assert obs.nhc_id == "al112017"
    assert obs.bin_number == "AT1"
    assert obs.name == "Irma"
    assert obs.classification == "HU"
    assert obs.intensity_kt == 160
    assert obs.pressure_mb == 927
    assert obs.latitude_deg == 22.9
    assert obs.longitude_deg == -79.9
    assert obs.movement_dir_deg == 315
    assert obs.movement_speed_mph == 15
    assert obs.last_update == datetime(2017, 9, 9, 15, 0, tzinfo=UTC)
    # Two advisory sub-products present, one missing (no windWatchesWarnings).
    assert set(obs.advisory_urls.keys()) >= {"publicAdvisory", "forecastTrack", "trackCone"}


def test_normalize_storm_stationary_storm_null_movement() -> None:
    """Stationary storms come through with missing or null movementDir/Speed.

    We've occasionally seen ``movementDir: null, movementSpeed: 0`` for a
    stalling hurricane — treat both as absent motion so the dashboard
    doesn't render a 0mph bearing-less arrow.
    """
    fixture = _load_fixture()
    raw = fixture["activeStorms"][0].copy()
    raw["movementDir"] = None
    raw["movementSpeed"] = None

    obs = _normalize_storm(raw)
    assert obs.movement_dir_deg is None
    assert obs.movement_speed_mph is None


def test_normalize_storm_no_pressure_degrades_gracefully() -> None:
    fixture = _load_fixture()
    raw = fixture["activeStorms"][0].copy()
    raw["pressure"] = None

    obs = _normalize_storm(raw)
    assert obs.pressure_mb is None
    # Everything else still populated:
    assert obs.intensity_kt == 160


@pytest.mark.parametrize(
    "field",
    ["id", "name", "classification", "lastUpdate"],
)
def test_normalize_storm_raises_on_missing_required_field(field: str) -> None:
    fixture = _load_fixture()
    raw = fixture["activeStorms"][0].copy()
    raw.pop(field)
    with pytest.raises(ValueError):
        _normalize_storm(raw)


def test_normalize_storm_raises_on_missing_required_numeric() -> None:
    fixture = _load_fixture()
    raw = fixture["activeStorms"][0].copy()
    raw.pop("latitude_numeric")
    with pytest.raises(ValueError, match="latitude_numeric"):
        _normalize_storm(raw)


# ----- _extract_active_storms ---------------------------------------------


def test_extract_active_storms_unwraps_documented_shape() -> None:
    storms = _extract_active_storms({"activeStorms": [{"id": "x"}]})
    assert storms == [{"id": "x"}]


def test_extract_active_storms_returns_empty_when_key_missing() -> None:
    # Some off-season payloads publish the wrapper object without the key.
    assert _extract_active_storms({}) == []


def test_extract_active_storms_accepts_bare_list_fallback() -> None:
    """Defensive: if NHC ever drops the wrapper, don't take the pipe offline."""
    storms = _extract_active_storms([{"id": "x"}])
    assert storms == [{"id": "x"}]


def test_extract_active_storms_raises_on_nonsense_type() -> None:
    with pytest.raises(NHCScrapeError):
        _extract_active_storms("oh no")  # type: ignore[arg-type]


def test_extract_active_storms_raises_when_key_is_not_a_list() -> None:
    with pytest.raises(NHCScrapeError, match="not a list"):
        _extract_active_storms({"activeStorms": {"id": "x"}})


# ----- fetch_active_storms -------------------------------------------------


def test_fetch_active_storms_normalizes_realistic_payload() -> None:
    fixture = _load_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/CurrentStorms.json")
        return httpx.Response(200, json=fixture)

    storms = fetch_active_storms(http_client=_make_client(handler))
    assert [s.name for s in storms] == ["Irma", "Jose"]
    assert {s.classification for s in storms} == {"HU"}


def test_fetch_active_storms_empty_feed_returns_empty_list() -> None:
    """Off-season is a normal state, not an error."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"activeStorms": []})

    storms = fetch_active_storms(http_client=_make_client(handler))
    assert storms == []


def test_fetch_active_storms_skips_malformed_storms(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One bad record shouldn't poison the batch — log and keep going."""

    def handler(_request: httpx.Request) -> httpx.Response:
        payload = {
            "activeStorms": [
                {
                    "id": "al112017",
                    "name": "Irma",
                    "classification": "HU",
                    "intensity": 160,
                    "latitude_numeric": 22.9,
                    "longitude_numeric": -79.9,
                    "lastUpdate": "2017-09-09T15:00:00Z",
                },
                # Missing id — should be skipped with a warning.
                {
                    "name": "Ghost",
                    "classification": "TS",
                    "intensity": 50,
                    "latitude_numeric": 10.0,
                    "longitude_numeric": -50.0,
                    "lastUpdate": "2017-09-09T15:00:00Z",
                },
                # Non-object entry — should also be skipped.
                "not an object",
            ],
        }
        return httpx.Response(200, json=payload)

    import logging as _logging

    with caplog.at_level(_logging.WARNING, logger="rmn_dashboard.scrapers.nhc"):
        storms = fetch_active_storms(http_client=_make_client(handler))

    assert [s.nhc_id for s in storms] == ["al112017"]
    assert any("malformed" in r.message for r in caplog.records)
    assert any("non-object" in r.message for r in caplog.records)


def test_fetch_active_storms_raises_on_http_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "maintenance"})

    with pytest.raises(httpx.HTTPStatusError):
        fetch_active_storms(http_client=_make_client(handler))


def test_fetch_active_storms_uses_custom_url() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"activeStorms": []})

    fetch_active_storms(
        http_client=_make_client(handler),
        url="https://example.test/alt-feed.json",
    )
    assert captured["url"] == "https://example.test/alt-feed.json"
