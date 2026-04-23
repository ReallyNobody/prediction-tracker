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

    # Six panels. The class appears once in the <style> block plus once
    # per panel, so seven matches total.
    assert body.count("panel-shell") == 7

    # Each panel heading is present.
    for heading in (
        "Active storms",
        "Markets on it",
        "Carrier exposure",
        "Cat bond spreads",
        "Historical analogs",
        "What changed today",
    ):
        assert heading in body, f"missing heading: {heading}"


def test_index_shows_empty_state_when_no_markets(client: TestClient) -> None:
    """With a fresh DB, the Markets panel renders its empty-state copy."""
    body = client.get("/").text
    assert "No hurricane markets in the database yet" in body


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
    assert "No hurricane markets in the database yet" not in body


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
