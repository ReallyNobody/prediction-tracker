"""NHC shapefile scraper — parse forecastTrack and wsp_120hr ZIPs.

NHC publishes its advisory spatial products as ZIP archives containing
ESRI shapefiles (.shp/.shx/.dbf triples plus an optional .prj).

Three products matter for the RMN dashboard:

* ``forecastTrack`` ZIP (per-storm, per-advisory):
    - ``*_5day_pgn.shp`` — the forecast cone polygon
    - ``*_5day_pts.shp`` — forecast center points at 12/24/36/48/72/96/120h
    - ``*_5day_lin.shp`` — the forecast track line (dashboard doesn't use)

* ``wsp_120hr`` ZIP (basin-scoped, not per-storm):
    - ``*wsp34knt120hr.shp`` / ``*wsp50knt120hr.shp`` / ``*wsp64knt120hr.shp``
      — probability contours for ≥34 kt, ≥50 kt, ≥64 kt sustained winds
      over the next 120 hours. One shapefile covers all active storms
      in the basin (Atlantic or East Pacific).

We use pyshp, a pure-Python reader, to avoid pulling GDAL/GEOS C-lib
wheels at Render build time. Conversion to GeoJSON is hand-rolled —
points and polygons are the only shape types NHC publishes for these
products, so a third-party geometry library is overkill.

Coordinates are WGS84 (EPSG:4326) on every NHC .prj we've inspected;
we don't reproject.

Network isolation: the fetch helpers take an optional ``httpx.Client``
so tests can inject a ``MockTransport`` — same contract as
``scrapers/nhc.py`` and ``scrapers/kalshi.py``.
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import shapefile  # pyshp

from rmn_dashboard.config import settings

logger = logging.getLogger(__name__)


# ----- Errors --------------------------------------------------------------


class NHCShapefileError(RuntimeError):
    """Raised when a shapefile ZIP is unparseable for a structural reason
    (missing expected layer, corrupt archive, wrong shape type).

    Per-record malformation is logged and skipped rather than raised —
    NHC occasionally publishes a single bad feature and we'd rather
    return the other six than take the whole ingest offline.
    """


# ----- Dataclasses ---------------------------------------------------------


@dataclass(frozen=True)
class ForecastTrack:
    """Normalized payload from a parsed forecastTrack ZIP.

    ``cone_geojson`` is a bare GeoJSON Polygon/MultiPolygon geometry
    object (not a Feature) — it goes straight into the
    ``Forecast.cone_geojson`` column.

    ``forecast_5day_points`` is a list of GeoJSON Features, each
    carrying the DBF record as properties. Storage goes into
    ``Forecast.forecast_5day_points`` as a JSON array.

    ``advisory_number`` and ``issued_at`` are lifted from the first
    point record's ADVISNUM / ADVDATE fields so the caller can dedupe
    on ``(storm_id, issued_at)`` without re-opening the ZIP.
    """

    advisory_number: str
    issued_at: datetime
    cone_geojson: dict[str, Any]
    forecast_5day_points: list[dict[str, Any]] = field(default_factory=list)


# ----- ZIP + shapefile helpers --------------------------------------------


# NHC fills missing numeric fields with sentinel values. Strip them at the
# GeoJSON boundary so downstream code doesn't have to remember the magic.
_DBF_NUMERIC_SENTINELS = {-9999, -99, 9999}


def _open_shapefile_from_zip(
    archive: zipfile.ZipFile,
    stem: str,
) -> shapefile.Reader:
    """Open a shapefile trio (.shp/.shx/.dbf) stored in ``archive`` under a
    common filename stem.

    pyshp accepts file-like objects for each component — we pass three
    ``BytesIO`` wrappers so nothing touches the filesystem. ``.shx`` is
    technically optional but NHC always ships it and pyshp's random
    access is much faster with it present.
    """
    shp = archive.read(f"{stem}.shp")
    dbf = archive.read(f"{stem}.dbf")
    try:
        shx = archive.read(f"{stem}.shx")
    except KeyError:
        shx = None

    kwargs: dict[str, Any] = {"shp": io.BytesIO(shp), "dbf": io.BytesIO(dbf)}
    if shx is not None:
        kwargs["shx"] = io.BytesIO(shx)
    return shapefile.Reader(**kwargs)


def _find_shapefile_stem(archive: zipfile.ZipFile, suffix: str) -> str | None:
    """Return the stem of the first ``*<suffix>.shp`` entry in the archive.

    NHC filenames embed the storm id + advisory number (e.g.
    ``al112017-038_5day_pgn.shp``), so we match by suffix rather than
    by exact name.
    """
    for name in archive.namelist():
        lname = name.lower()
        if lname.endswith(f"{suffix}.shp"):
            return name[: -len(".shp")]
    return None


def _find_all_shapefile_stems(archive: zipfile.ZipFile, contains: str) -> list[str]:
    """Return every shapefile stem whose name contains ``contains`` (case-
    insensitive). Used for wsp_120hr, which ships multiple threshold layers
    (wsp34knt/wsp50knt/wsp64knt) in one ZIP.
    """
    stems: list[str] = []
    for name in archive.namelist():
        lname = name.lower()
        if contains.lower() in lname and lname.endswith(".shp"):
            stems.append(name[: -len(".shp")])
    return stems


# ----- DBF record → GeoJSON properties ------------------------------------


def _record_to_properties(record: shapefile.Record, field_names: list[str]) -> dict[str, Any]:
    """Pull a pyshp Record into a plain dict, stripping numeric sentinels.

    pyshp exposes records as both dict-like and list-like; we use the
    field-name iteration because NHC DBF schemas occasionally reorder
    columns between product versions.
    """
    props: dict[str, Any] = {}
    for idx, fname in enumerate(field_names):
        value = record[idx]
        # Strip NHC sentinels (-9999, etc.) for numeric fields so the
        # frontend can distinguish "no data" from a real value.
        if isinstance(value, int | float) and value in _DBF_NUMERIC_SENTINELS:
            props[fname] = None
        elif isinstance(value, bytes):
            # pyshp returns bytes for some string fields depending on the
            # encoding flag; decode defensively.
            props[fname] = value.decode("utf-8", errors="replace").strip()
        elif isinstance(value, str):
            props[fname] = value.strip()
        else:
            props[fname] = value
    return props


def _dbf_field_names(reader: shapefile.Reader) -> list[str]:
    """pyshp's ``fields`` list starts with a ``DeletionFlag`` header we
    don't want; this helper returns just the real field names in order."""
    # Each field is (name, type, size, decimal); skip any whose name is
    # "DeletionFlag".
    return [f[0] for f in reader.fields if f[0] != "DeletionFlag"]


# ----- Shape → GeoJSON geometry -------------------------------------------


def _shape_to_geojson_geometry(shape: shapefile.Shape) -> dict[str, Any]:
    """Convert a pyshp ``Shape`` into a GeoJSON-compatible geometry dict.

    Handles:
      * POINT (shapeType 1)
      * POLYLINE (shapeType 3) → LineString / MultiLineString
      * POLYGON (shapeType 5) → Polygon / MultiPolygon
      * MULTIPOINT (shapeType 8)

    Raises on unsupported shape types; NHC only publishes points and
    polygons for the products we consume, so if we hit something else
    it's either a schema change or the wrong ZIP.
    """
    sh_type = shape.shapeType
    if sh_type == shapefile.POINT:
        x, y = shape.points[0]
        return {"type": "Point", "coordinates": [x, y]}

    if sh_type == shapefile.MULTIPOINT:
        return {
            "type": "MultiPoint",
            "coordinates": [[x, y] for x, y in shape.points],
        }

    if sh_type == shapefile.POLYLINE:
        parts = _split_parts(shape.points, shape.parts)
        if len(parts) == 1:
            return {"type": "LineString", "coordinates": parts[0]}
        return {"type": "MultiLineString", "coordinates": parts}

    if sh_type == shapefile.POLYGON:
        parts = _split_parts(shape.points, shape.parts)
        # pyshp doesn't distinguish rings that belong to the same polygon
        # from separate polygons — NHC cones are always single-polygon,
        # multi-ring (outer + optional hole) or single-ring. Treat a
        # single part as a Polygon with one ring; multi-part as MultiPolygon
        # where each ring is its own outer boundary. This is a pragmatic
        # choice that matches what the dashboard actually renders.
        if len(parts) == 1:
            return {"type": "Polygon", "coordinates": [parts[0]]}
        return {"type": "MultiPolygon", "coordinates": [[p] for p in parts]}

    raise NHCShapefileError(f"Unsupported shapefile shape type: {sh_type}")


def _split_parts(points: list[tuple[float, float]], parts: list[int]) -> list[list[list[float]]]:
    """Break a flat list of points into rings/linestrings at each ``parts``
    index. pyshp stores multi-part geometries as a single flat ``points``
    array plus a list of start indices; GeoJSON wants nested arrays."""
    if not parts:
        return [[[x, y] for x, y in points]]
    segments: list[list[list[float]]] = []
    for i, start in enumerate(parts):
        end = parts[i + 1] if i + 1 < len(parts) else len(points)
        segments.append([[x, y] for x, y in points[start:end]])
    return segments


# ----- ADVDATE parsing -----------------------------------------------------


def _parse_advdate(raw: str) -> datetime:
    """NHC's ADVDATE is typically one of these formats:

        '170909 1500'         # yyMMdd HHMM (UTC, no seconds)
        '1500 UTC SAT SEP 09 2017'   # verbose form
        '2017-09-09T15:00:00Z'       # ISO form (newer shapefiles)

    Try the cheap ones first; fall back to ISO.
    """
    candidates = (
        "%y%m%d %H%M",
        "%H%M UTC %a %b %d %Y",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
    )
    for fmt in candidates:
        try:
            dt = datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
        # Force UTC — NHC never publishes local time.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    raise ValueError(f"Unparseable ADVDATE: {raw!r}")


# ----- parse_forecast_track_zip -------------------------------------------


def parse_forecast_track_zip(zip_bytes: bytes) -> ForecastTrack:
    """Parse a forecastTrack ZIP into a ``ForecastTrack`` dataclass.

    Required layers inside the ZIP:

      * ``*_5day_pts.shp`` — source of truth for ``advisory_number`` and
        ``issued_at``, plus the list of forecast center points.
      * ``*_5day_pgn.shp`` — the cone polygon.

    Either missing raises ``NHCShapefileError``. A single corrupt record
    inside an otherwise-valid shapefile is logged and skipped.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise NHCShapefileError("forecastTrack payload is not a valid ZIP") from exc

    pts_stem = _find_shapefile_stem(archive, "_5day_pts")
    pgn_stem = _find_shapefile_stem(archive, "_5day_pgn")
    if pts_stem is None or pgn_stem is None:
        raise NHCShapefileError(
            "forecastTrack ZIP missing expected _5day_pts/_5day_pgn layers; "
            f"found {archive.namelist()!r}"
        )

    points = _parse_point_layer(archive, pts_stem)
    if not points:
        raise NHCShapefileError("forecastTrack _5day_pts layer has no features")

    # Identity lives on the first point record — NHC guarantees advisory
    # metadata is consistent across every row in a given shapefile.
    first_props = points[0]["properties"]
    advisory_number = str(first_props.get("ADVISNUM") or first_props.get("advisnum") or "")
    advdate_raw = first_props.get("ADVDATE") or first_props.get("advdate") or ""
    if not advisory_number or not advdate_raw:
        raise NHCShapefileError(
            f"forecastTrack point record missing ADVISNUM or ADVDATE: {first_props!r}"
        )

    issued_at = _parse_advdate(advdate_raw)
    cone_geojson = _parse_cone_layer(archive, pgn_stem)

    return ForecastTrack(
        advisory_number=advisory_number,
        issued_at=issued_at,
        cone_geojson=cone_geojson,
        forecast_5day_points=points,
    )


def _parse_point_layer(archive: zipfile.ZipFile, stem: str) -> list[dict[str, Any]]:
    """Read every point feature in ``stem`` into GeoJSON Feature dicts."""
    reader = _open_shapefile_from_zip(archive, stem)
    fnames = _dbf_field_names(reader)
    features: list[dict[str, Any]] = []
    for shape_rec in reader.shapeRecords():
        try:
            geom = _shape_to_geojson_geometry(shape_rec.shape)
        except NHCShapefileError:
            logger.warning(
                "Skipping non-point feature in %s: shapeType=%s",
                stem,
                shape_rec.shape.shapeType,
            )
            continue
        props = _record_to_properties(shape_rec.record, fnames)
        features.append({"type": "Feature", "geometry": geom, "properties": props})
    return features


def _parse_cone_layer(archive: zipfile.ZipFile, stem: str) -> dict[str, Any]:
    """Read the first polygon feature in ``stem`` and return its geometry.

    NHC's cone shapefile always contains a single feature per advisory;
    if we ever see more, log the ones we drop — the cone is the first.
    """
    reader = _open_shapefile_from_zip(archive, stem)
    shape_records = list(reader.shapeRecords())
    if not shape_records:
        raise NHCShapefileError(f"{stem} has no features")
    if len(shape_records) > 1:
        logger.info(
            "%s has %d features; using the first (cone polygon) and ignoring rest",
            stem,
            len(shape_records),
        )
    return _shape_to_geojson_geometry(shape_records[0].shape)


# ----- parse_wind_probability_zip -----------------------------------------


_WSP_THRESHOLD_BY_SUBSTRING = {
    "wsp34knt": 34,
    "wsp50knt": 50,
    "wsp64knt": 64,
}


def parse_wind_probability_zip(zip_bytes: bytes) -> dict[str, Any]:
    """Parse a wsp_120hr ZIP into a GeoJSON FeatureCollection.

    NHC ships one shapefile per threshold (34 / 50 / 64 kt) inside the
    basin's wind-probability ZIP. We flatten all of them into a single
    FeatureCollection, tagging each feature with a ``threshold_kt``
    property so the frontend can filter client-side.

    Empty ZIPs (basin with no active wind hazards) return a valid empty
    FeatureCollection rather than raising — it's an off-season-ish state
    that the dashboard should render as "no probability data."
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise NHCShapefileError("wsp_120hr payload is not a valid ZIP") from exc

    features: list[dict[str, Any]] = []
    stems = _find_all_shapefile_stems(archive, "wsp")
    if not stems:
        raise NHCShapefileError(f"wsp_120hr ZIP has no wsp*.shp layers: {archive.namelist()!r}")

    for stem in stems:
        lower = stem.lower()
        threshold = None
        for substring, kt in _WSP_THRESHOLD_BY_SUBSTRING.items():
            if substring in lower:
                threshold = kt
                break
        # Cumulative ("any threshold") layer stays tagged as None —
        # frontend treats None as "any wind hazard".

        reader = _open_shapefile_from_zip(archive, stem)
        fnames = _dbf_field_names(reader)
        for shape_rec in reader.shapeRecords():
            try:
                geom = _shape_to_geojson_geometry(shape_rec.shape)
            except NHCShapefileError:
                logger.warning(
                    "Skipping non-polygon feature in %s: shapeType=%s",
                    stem,
                    shape_rec.shape.shapeType,
                )
                continue
            props = _record_to_properties(shape_rec.record, fnames)
            props["threshold_kt"] = threshold
            features.append({"type": "Feature", "geometry": geom, "properties": props})

    return {"type": "FeatureCollection", "features": features}


# ----- Fetch wrappers -----------------------------------------------------


def _default_client() -> httpx.Client:
    """Same courtesy User-Agent as ``scrapers/nhc.py`` — NHC doesn't require
    one but the SEC-style contact string is polite and buys goodwill if
    we ever need to ask for a rate-limit bump."""
    return httpx.Client(headers={"User-Agent": settings.sec_user_agent}, timeout=30.0)


def fetch_zip_bytes(url: str, http_client: httpx.Client | None = None) -> bytes:
    """Fetch an NHC ZIP URL and return the raw bytes.

    Kept as a thin wrapper so tests can mock the fetch cleanly via
    ``httpx.MockTransport``.
    """
    client = http_client or _default_client()
    owns_client = http_client is None
    try:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content
    finally:
        if owns_client:
            client.close()


def fetch_forecast_track(url: str, http_client: httpx.Client | None = None) -> ForecastTrack:
    """Fetch + parse a forecastTrack ZIP in one step."""
    return parse_forecast_track_zip(fetch_zip_bytes(url, http_client=http_client))


def fetch_wind_probability(url: str, http_client: httpx.Client | None = None) -> dict[str, Any]:
    """Fetch + parse a wsp_120hr ZIP in one step."""
    return parse_wind_probability_zip(fetch_zip_bytes(url, http_client=http_client))
