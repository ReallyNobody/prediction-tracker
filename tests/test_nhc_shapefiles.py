"""Unit tests for the NHC shapefile scraper.

Fixture strategy: we build synthetic forecastTrack and wsp_120hr ZIPs
in memory via pyshp's ``Writer``, mirroring the real NHC schema.
Keeping the builders in-test makes the schema assumptions explicit —
if NHC introduces a field, the test breaks before production.

Never hits the network: httpx requests flow through ``MockTransport``.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest
import shapefile  # pyshp

from rmn_dashboard.scrapers.nhc_shapefiles import (
    ForecastTrack,
    NHCShapefileError,
    _parse_advdate,
    fetch_forecast_track,
    fetch_wind_probability,
    fetch_zip_bytes,
    parse_forecast_track_zip,
    parse_wind_probability_zip,
)

# ----- Synthetic fixture builders -----------------------------------------


def _write_shapefile_trio(
    shape_type: int,
    fields: list[tuple],
    features: list[tuple],
) -> dict[str, bytes]:
    """Build an in-memory shapefile trio (shp/shx/dbf) and return the bytes.

    ``fields`` entries are ``(name, type, size, decimal?)``. ``features``
    entries are ``(geom, record_tuple)`` — ``geom`` is a coord pair for
    POINT, or a ring (list of coord pairs) for POLYGON.
    """
    shp = io.BytesIO()
    shx = io.BytesIO()
    dbf = io.BytesIO()
    writer = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shape_type)
    for spec in fields:
        fname, ftype, *rest = spec
        kwargs: dict = {}
        if rest:
            kwargs["size"] = rest[0]
        if len(rest) > 1:
            kwargs["decimal"] = rest[1]
        writer.field(fname, ftype, **kwargs)
    for geom, record in features:
        if shape_type == shapefile.POINT:
            writer.point(*geom)
        elif shape_type == shapefile.POLYGON:
            writer.poly([geom])
        else:  # pragma: no cover — unused in these tests
            raise ValueError(f"Unsupported shape_type in test builder: {shape_type}")
        writer.record(*record)
    writer.close()
    return {"shp": shp.getvalue(), "shx": shx.getvalue(), "dbf": dbf.getvalue()}


def _zip_shapefiles(layers: dict[str, dict[str, bytes]]) -> bytes:
    """Wrap one or more shapefile trios into a single ZIP archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for stem, parts in layers.items():
            for ext, data in parts.items():
                archive.writestr(f"{stem}.{ext}", data)
    return buf.getvalue()


def _build_forecast_track_zip(
    *,
    storm_id: str = "al112017",
    advisory_number: str = "038",
    advdate: str = "170909 1500",
    point_features: list[tuple[tuple[float, float], tuple]] | None = None,
    cone_ring: list[list[float]] | None = None,
) -> bytes:
    """Build a realistic forecastTrack ZIP containing ``_5day_pts`` and
    ``_5day_pgn`` layers. Defaults mimic Irma advisory 38 (2017-09-09)."""
    if point_features is None:
        # (geom, record) — record order matches ``pts_fields`` below.
        point_features = [
            ((-79.9, 22.9), (advisory_number, advdate, 0, 22.9, -79.9, 160, "HU")),
            ((-81.0, 24.5), (advisory_number, advdate, 12, 24.5, -81.0, 150, "HU")),
            ((-82.5, 26.5), (advisory_number, advdate, 24, 26.5, -82.5, 130, "HU")),
        ]
    if cone_ring is None:
        cone_ring = [
            [-84.0, 20.0],
            [-76.0, 20.0],
            [-76.0, 30.0],
            [-84.0, 30.0],
            [-84.0, 20.0],
        ]

    # Field sizes mirror what real NHC DBFs publish. MAXWIND in particular
    # must be wide enough to hold the sentinel value (-9999); too narrow
    # and pyshp will silently truncate the sign off.
    pts_fields = [
        ("ADVISNUM", "C", 4),
        ("ADVDATE", "C", 30),
        ("TAU", "N", 4),
        ("LAT", "N", 8, 3),
        ("LON", "N", 8, 3),
        ("MAXWIND", "N", 6),
        ("STORMTYPE", "C", 4),
    ]
    pgn_fields = [
        ("ADVISNUM", "C", 4),
        ("ADVDATE", "C", 30),
        ("STORMNAME", "C", 24),
    ]

    pts_trio = _write_shapefile_trio(shapefile.POINT, pts_fields, point_features)
    pgn_trio = _write_shapefile_trio(
        shapefile.POLYGON,
        pgn_fields,
        [(cone_ring, (advisory_number, advdate, "Irma"))],
    )
    return _zip_shapefiles(
        {
            f"{storm_id}-{advisory_number}_5day_pts": pts_trio,
            f"{storm_id}-{advisory_number}_5day_pgn": pgn_trio,
        }
    )


def _build_wind_probability_zip(
    *,
    thresholds: tuple[int, ...] = (34, 50, 64),
    ring: list[list[float]] | None = None,
) -> bytes:
    """Build a wsp_120hr ZIP with one polygon per requested threshold."""
    if ring is None:
        ring = [
            [-85.0, 20.0],
            [-75.0, 20.0],
            [-75.0, 30.0],
            [-85.0, 30.0],
            [-85.0, 20.0],
        ]
    fields = [
        ("PWIND", "N", 4),
    ]
    layers: dict[str, dict[str, bytes]] = {}
    for kt in thresholds:
        trio = _write_shapefile_trio(
            shapefile.POLYGON,
            fields,
            [(ring, (80,))],
        )
        layers[f"2017wsp{kt}knt120hr_halfDeg"] = trio
    return _zip_shapefiles(layers)


def _client_for_bytes(payload: bytes) -> httpx.Client:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _client_for_fn(fn: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(fn))


# ----- _parse_advdate ------------------------------------------------------


def test_parse_advdate_yymmdd_hhmm_form() -> None:
    assert _parse_advdate("170909 1500") == datetime(2017, 9, 9, 15, 0, tzinfo=UTC)


def test_parse_advdate_verbose_form() -> None:
    assert _parse_advdate("1500 UTC SAT SEP 09 2017") == datetime(2017, 9, 9, 15, 0, tzinfo=UTC)


def test_parse_advdate_iso_form() -> None:
    assert _parse_advdate("2017-09-09T15:00:00Z") == datetime(2017, 9, 9, 15, 0, tzinfo=UTC)


def test_parse_advdate_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="Unparseable ADVDATE"):
        _parse_advdate("whenever")


# ----- parse_forecast_track_zip -------------------------------------------


def test_parse_forecast_track_happy_path() -> None:
    zip_bytes = _build_forecast_track_zip()

    result = parse_forecast_track_zip(zip_bytes)

    assert isinstance(result, ForecastTrack)
    assert result.advisory_number == "038"
    assert result.issued_at == datetime(2017, 9, 9, 15, 0, tzinfo=UTC)

    # Cone geometry is a Polygon with one outer ring.
    assert result.cone_geojson["type"] == "Polygon"
    assert len(result.cone_geojson["coordinates"]) == 1
    assert result.cone_geojson["coordinates"][0][0] == [-84.0, 20.0]

    # Three forecast points, each a Feature with geometry + properties.
    assert len(result.forecast_5day_points) == 3
    first = result.forecast_5day_points[0]
    assert first["type"] == "Feature"
    assert first["geometry"]["type"] == "Point"
    assert first["geometry"]["coordinates"] == [-79.9, 22.9]
    assert first["properties"]["ADVISNUM"] == "038"
    assert first["properties"]["TAU"] == 0
    assert first["properties"]["MAXWIND"] == 160
    assert first["properties"]["STORMTYPE"] == "HU"


def test_parse_forecast_track_strips_numeric_sentinels() -> None:
    """NHC fills missing numerics with -9999; they must come through as None."""
    features = [
        ((-79.9, 22.9), ("038", "170909 1500", 0, 22.9, -79.9, -9999, "HU")),
    ]
    zip_bytes = _build_forecast_track_zip(point_features=features)

    result = parse_forecast_track_zip(zip_bytes)

    assert result.forecast_5day_points[0]["properties"]["MAXWIND"] is None
    # Real values are preserved.
    assert result.forecast_5day_points[0]["properties"]["LAT"] == 22.9


def test_parse_forecast_track_raises_on_missing_layer() -> None:
    # Build a ZIP that only has the points layer, not the cone.
    trio = _write_shapefile_trio(
        shapefile.POINT,
        [("ADVISNUM", "C", 4), ("ADVDATE", "C", 30), ("LAT", "N", 8, 3), ("LON", "N", 8, 3)],
        [((-79.9, 22.9), ("038", "170909 1500", 22.9, -79.9))],
    )
    zip_bytes = _zip_shapefiles({"al112017-038_5day_pts": trio})

    with pytest.raises(NHCShapefileError, match="missing expected"):
        parse_forecast_track_zip(zip_bytes)


def test_parse_forecast_track_raises_on_bad_zip() -> None:
    with pytest.raises(NHCShapefileError, match="not a valid ZIP"):
        parse_forecast_track_zip(b"this is not a zip file")


def test_parse_forecast_track_raises_on_empty_points_layer() -> None:
    zip_bytes = _build_forecast_track_zip(point_features=[])
    with pytest.raises(NHCShapefileError, match="no features"):
        parse_forecast_track_zip(zip_bytes)


def test_parse_forecast_track_raises_on_missing_advisory_metadata() -> None:
    features = [
        ((-79.9, 22.9), ("", "", 0, 22.9, -79.9, 160, "HU")),
    ]
    zip_bytes = _build_forecast_track_zip(point_features=features)
    with pytest.raises(NHCShapefileError, match="ADVISNUM or ADVDATE"):
        parse_forecast_track_zip(zip_bytes)


# ----- parse_wind_probability_zip -----------------------------------------


def test_parse_wind_probability_happy_path() -> None:
    zip_bytes = _build_wind_probability_zip()

    result = parse_wind_probability_zip(zip_bytes)

    assert result["type"] == "FeatureCollection"
    # One feature per threshold shapefile (34/50/64 kt).
    assert len(result["features"]) == 3
    thresholds = {f["properties"]["threshold_kt"] for f in result["features"]}
    assert thresholds == {34, 50, 64}
    # Each feature has a Polygon geometry and the PWIND property.
    for feature in result["features"]:
        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] == "Polygon"
        assert feature["properties"]["PWIND"] == 80


def test_parse_wind_probability_single_threshold() -> None:
    """Some archived advisories ship only the 34kt layer."""
    zip_bytes = _build_wind_probability_zip(thresholds=(34,))

    result = parse_wind_probability_zip(zip_bytes)

    assert len(result["features"]) == 1
    assert result["features"][0]["properties"]["threshold_kt"] == 34


def test_parse_wind_probability_raises_on_no_layers() -> None:
    # ZIP exists but contains nothing wsp-shaped.
    trio = _write_shapefile_trio(
        shapefile.POLYGON,
        [("FOO", "C", 4)],
        [([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]], ("bar",))],
    )
    zip_bytes = _zip_shapefiles({"unrelated": trio})
    with pytest.raises(NHCShapefileError, match="no wsp"):
        parse_wind_probability_zip(zip_bytes)


def test_parse_wind_probability_raises_on_bad_zip() -> None:
    with pytest.raises(NHCShapefileError, match="not a valid ZIP"):
        parse_wind_probability_zip(b"\x00\x01\x02 nope")


# ----- fetch_zip_bytes + network wrappers ---------------------------------


def test_fetch_zip_bytes_returns_payload() -> None:
    payload = b"\x50\x4b\x03\x04 fake zip bytes"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(".zip")
        return httpx.Response(200, content=payload)

    result = fetch_zip_bytes(
        "https://www.nhc.noaa.gov/gis/forecast/archive/al112017_5day_038.zip",
        http_client=_client_for_fn(handler),
    )
    assert result == payload


def test_fetch_zip_bytes_raises_on_http_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"maintenance")

    with pytest.raises(httpx.HTTPStatusError):
        fetch_zip_bytes("https://example.test/foo.zip", http_client=_client_for_fn(handler))


def test_fetch_forecast_track_combines_fetch_and_parse() -> None:
    zip_bytes = _build_forecast_track_zip()

    result = fetch_forecast_track(
        "https://example.test/al112017_5day_038.zip",
        http_client=_client_for_bytes(zip_bytes),
    )
    assert isinstance(result, ForecastTrack)
    assert result.advisory_number == "038"
    assert len(result.forecast_5day_points) == 3


def test_fetch_wind_probability_combines_fetch_and_parse() -> None:
    zip_bytes = _build_wind_probability_zip()

    result = fetch_wind_probability(
        "https://example.test/2017_wsp_120hr.zip",
        http_client=_client_for_bytes(zip_bytes),
    )
    assert result["type"] == "FeatureCollection"
    assert len(result["features"]) == 3
