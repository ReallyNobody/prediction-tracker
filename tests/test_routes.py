"""Integration tests for HTTP routes.

Uses the shared ``client`` fixture (see ``conftest.py``), which overrides
``get_session`` so the app talks to a per-test in-memory SQLite DB with
the full ORM schema already applied. When a test needs to seed rows,
it also requests ``db_session`` — both fixtures depend on the same
``_test_engine`` fixture, so they share the underlying database.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from rmn_dashboard.models import PredictionMarket


def test_index_returns_html_with_panel_shells(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")

    body = response.text
    assert "<html" in body.lower()
    assert "Hurricane Dashboard" in body

    # Seven panels (six original + the Day 12 landfall map). The class
    # appears once in the <style> block plus once per panel, so eight
    # matches total.
    assert body.count("panel-shell") == 8

    # Each panel heading is present. Day 14 renamed Carrier Exposure →
    # Companies on the line; Day 15 renamed Cat Bond Spreads → Cat bond
    # market when we pivoted from gated index data to a public ETF proxy;
    # Day 20 renamed Cat bond market → Hurricane risk capital when we
    # added KBWP (P&C insurance index ETF) as a second row alongside ILS;
    # Day 31 renamed "Markets on it" → "Prediction Markets" (clearer
    # category term, less voicy, matches what readers will Google).
    for heading in (
        "Active storms",
        "Prediction Markets",
        "Landfall probability",
        "Companies on the line",
        "Hurricane risk capital",
        "Historical analogs",
        "What changed today",
    ):
        assert heading in body, f"missing heading: {heading}"


def test_index_shows_empty_state_when_no_markets(client: TestClient) -> None:
    """With a fresh DB, the Markets panel renders its empty-state copy."""
    body = client.get("/").text
    # Day 18 softened this from dev-y "run the Kalshi ingest job" wording
    # to reader-facing copy. The phrase below is the editorial anchor.
    assert "No hurricane prediction markets are open right now" in body


def test_index_renders_market_rows_when_seeded(client: TestClient, db_session: Session) -> None:
    """Seed one snapshot via ``db_session`` (shares the test engine with the
    TestClient) then assert the panel renders the title, Yes price as cents,
    open interest, and the Kalshi link."""
    db_session.add(
        PredictionMarket(
            platform="kalshi",
            ticker="KXHURCTOT-26DEC01-T7",
            event_ticker="KXHURCTOT-26DEC01",
            title="Will there be more than 7 Atlantic hurricanes in 2026?",
            category="hurricane",
            yes_price=0.42,
            no_price=0.58,
            open_interest=269.0,
            last_updated=datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
        )
    )
    db_session.commit()

    body = client.get("/").text
    assert "Will there be more than 7 Atlantic hurricanes in 2026?" in body
    assert "kalshi.com/markets/KXHURCTOT-26DEC01-T7" in body
    assert "42¢" in body  # yes_price formatted as cents
    assert "269" in body  # open interest
    # Empty-state copy should be gone now.
    assert "No hurricane prediction markets are open right now" not in body


def test_index_wires_up_forecast_map(client: TestClient) -> None:
    """Panel 1 ships the Leaflet map container and the loader script.

    This is a smoke test — it doesn't exercise the JS, just asserts the
    HTML contract the script depends on. If any of these IDs, script
    paths, or the Leaflet CDN link disappear, the map silently won't
    render, so failing fast at the template level is worth the handful
    of string assertions.
    """
    body = client.get("/").text
    # Container + empty-state + advisory readout the JS targets by id.
    assert 'id="forecast-map"' in body
    assert 'id="forecast-map-empty"' in body
    assert 'id="forecast-map-advisory"' in body
    # Per-storm details readout under the map (name, category, winds,
    # pressure, movement) — filled by the same loader that draws the
    # cone, so it's wired up in the same template block.
    assert 'id="forecast-storm-details"' in body
    assert 'data-testid="forecast-storm-details"' in body
    # The Leaflet CDN + SRI bundle lives in base.html.
    assert "unpkg.com/leaflet@1.9.4" in body
    # The client-side loader that consumes /api/v1/forecasts/active.
    assert "/static/js/forecast_map.js" in body


def test_index_wires_up_equities_panel(client: TestClient) -> None:
    """Panel 2 (Companies on the line) ships its sector-filter pills,
    ticker grid container, empty-state div, as-of readout, and the
    loader script.

    Smoke test only — doesn't exercise the JS. ``panel_equities.js``
    targets each of these IDs by string, and the four sector pills'
    ``data-sector`` values must match the sector Literal in
    ``data/universe.py`` exactly. Losing any of them silently breaks
    the panel.
    """
    body = client.get("/").text
    # Container + empty-state + as-of readout.
    assert 'id="equities-grid"' in body
    assert 'id="equities-empty"' in body
    assert 'id="equities-as-of"' in body
    # Sector filter pills — values must match the Sector Literal in
    # data/universe.py, plus an "all" reset pill.
    assert 'id="equities-sector-pills"' in body
    for sector in ("all", "insurer", "reinsurer", "homebuilder", "utility"):
        assert f'data-sector="{sector}"' in body, f"missing sector pill: {sector}"
    # Loader script.
    assert "/static/js/panel_equities.js" in body


def test_index_wires_up_analogs_panel(client: TestClient) -> None:
    """Panel 5 (Historical analogs) ships its readout container, empty
    state, framing label, and the loader script.

    Smoke test only — no JS exercised. ``panel_analogs.js`` targets
    each of these IDs by string and pulls /api/v1/analogs.
    """
    body = client.get("/").text
    assert 'id="analogs-readout"' in body
    assert 'id="analogs-empty"' in body
    assert 'id="analogs-framing"' in body
    assert "/static/js/panel_analogs.js" in body


def test_analogs_endpoint_returns_offseason_payload(client: TestClient) -> None:
    """``/api/v1/analogs`` on a fresh DB (no active storms) responds
    in offseason mode with non-empty analogs.
    """
    response = client.get("/api/v1/analogs")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"mode", "framing", "analogs"}
    assert body["mode"] == "offseason"
    assert isinstance(body["analogs"], list)
    assert len(body["analogs"]) >= 1


def test_index_wires_up_changes_panel(client: TestClient) -> None:
    """Panel 6 (What changed today) ships its readout container, empty
    state, as-of label, and the loader script.

    Smoke test only — no JS exercised. ``panel_changes.js`` targets
    each of these IDs by string and pulls /api/v1/changes/today.
    """
    body = client.get("/").text
    assert 'id="changes-readout"' in body
    assert 'id="changes-empty"' in body
    assert 'id="changes-as-of"' in body
    assert "/static/js/panel_changes.js" in body


def test_changes_endpoint_returns_expected_shape(client: TestClient) -> None:
    """``/api/v1/changes/today`` responds with the four-key payload
    shape ``panel_changes.js`` reads. Fresh DB → empty lists / null.
    """
    response = client.get("/api/v1/changes/today")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"as_of", "storms", "equities", "cat_bond"}
    assert body["storms"] == []
    assert body["equities"] == []
    assert body["cat_bond"] is None


def test_index_wires_up_risk_capital_panel(client: TestClient) -> None:
    """Panel 3 (Hurricane risk capital) ships its readout container,
    empty state, as-of label, and the loader script.

    Smoke test only — no JS exercised. ``panel_risk_capital.js`` targets
    each of these IDs by string and pulls
    /api/v1/quotes/hurricane-universe?sectors=cat_bond_etf,pc_index, so
    losing any of them silently breaks the panel.

    Day 20 rename: this test was previously
    ``test_index_wires_up_cat_bonds_panel`` against IDs ``cat-bonds-*``
    when the panel was a single-row cat bond ETF readout. Adding KBWP
    as a second row reframed the panel and the IDs went with it.
    """
    body = client.get("/").text
    assert 'id="risk-capital-readout"' in body
    assert 'id="risk-capital-empty"' in body
    assert 'id="risk-capital-as-of"' in body
    assert "/static/js/panel_risk_capital.js" in body


def test_index_wires_up_landfall_map(client: TestClient) -> None:
    """Panel 4 (landfall probability) ships its own Leaflet container,
    threshold selector, and loader script.

    Separate smoke test from Panel 1's so a regression in one panel's
    markup fails on a distinct test rather than disappearing into a
    shared assertion block. ``panel_landfall.js`` targets each of these
    IDs by string — losing any of them silently breaks the map.
    """
    body = client.get("/").text
    # Container + empty-state + threshold selector the JS targets by id.
    assert 'id="landfall-map"' in body
    assert 'id="landfall-map-empty"' in body
    assert 'id="landfall-threshold"' in body
    # All three WSP threshold options are rendered — the dropdown is how
    # the user switches between 34 / 50 / 64 kt bands, so a regression
    # that drops one should fail loud rather than merely losing a choice.
    assert 'value="34"' in body
    assert 'value="50"' in body
    assert 'value="64"' in body
    # The client-side loader that consumes
    # /api/v1/forecasts/active?include_wsp=true and renders the choropleth.
    assert "/static/js/panel_landfall.js" in body


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_supports_head_request(client: TestClient) -> None:
    """``HEAD /`` returns 200 with an empty body. Day 30 fix: probes,
    link-preview crawlers, and curl-with-default-flags all issue HEAD
    requests on the root; before this fix the GET-only handler
    responded 405 Method Not Allowed and littered the access log.

    Per HTTP spec, the response should carry the same headers as GET
    but no body — starlette/FastAPI handles the body-stripping
    automatically when methods=["GET", "HEAD"] is declared on the
    route.
    """
    response = client.head("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.content == b""


def test_index_ships_share_card_meta(client: TestClient) -> None:
    """The index page advertises Open Graph + Twitter card metadata
    so links preview cleanly when shared on social / chat platforms.

    Smoke test only — we assert the tags are present, not that any
    specific platform's renderer accepts them. A full preview check
    is a manual pre-launch step (Twitter Card Validator / Facebook
    Sharing Debugger).
    """
    body = client.get("/").text

    # Author + theme + canonical — small but visible to crawlers and
    # mobile browsers (theme-color tints the URL bar on Android).
    assert 'name="author"' in body
    assert 'name="theme-color"' in body
    assert 'rel="canonical"' in body

    # Open Graph: title, description, url, image, site_name, type are
    # the minimum viable card. og:image dimensions are required by
    # several crawlers to decide whether to render the large variant.
    # Day 22 added og:image:type so crawlers know it's PNG without
    # sniffing the URL.
    for meta in (
        'property="og:title"',
        'property="og:description"',
        'property="og:url"',
        'property="og:image"',
        'property="og:image:type"',
        'property="og:image:width"',
        'property="og:image:height"',
        'property="og:site_name"',
        'property="og:type"',
    ):
        assert meta in body, f"missing OG tag: {meta}"

    # The OG image is served from /static/, not embedded inline. Day 22
    # switched the canonical reference from og-image.svg to og-image.png
    # so Twitter's summary_large_image card has a raster source.
    assert "og-image.png" in body

    # Twitter card. Day 22 switched from `summary` (no image, SVG-era
    # fallback) to `summary_large_image`, which Twitter renders with
    # the PNG above as a full-width preview. summary_large_image
    # requires an explicit twitter:image — assert both.
    assert 'content="summary_large_image"' in body
    assert 'name="twitter:title"' in body
    assert 'name="twitter:description"' in body
    assert 'name="twitter:image"' in body

    # The OG image is served from /static/, not embedded inline.
    assert "og-image.svg" in body
