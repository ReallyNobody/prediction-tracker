"""Integration tests for HTTP routes.

Uses the shared ``client`` fixture (see ``conftest.py``), which overrides
``get_session`` so the app talks to a per-test in-memory SQLite DB with
the full ORM schema already applied.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


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


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
