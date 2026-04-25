"""Integration tests for ``/api/v1/quotes/hurricane-universe``.

Exercises the route against a real ``TestClient`` so we cover query
parsing, sector validation, and JSON serialization end-to-end. The
service layer logic is tested separately in
``test_equity_quotes_service.py``; these tests focus on the HTTP
contract.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from rmn_dashboard.data.universe import load_universe
from rmn_dashboard.models import TickerQuote


def _seed_quote(db: Session, ticker: str, *, last_price: float) -> None:
    """Single-row helper — every test seeds at least one quote so the
    response shape's ``"quote"`` field is exercised non-null.
    """
    db.add(
        TickerQuote(
            ticker=ticker,
            last_price=last_price,
            prior_close=last_price - 1,
            change_amount=1.0,
            change_percent=round(1.0 / (last_price - 1) * 100, 4),
            currency="USD",
            source="test",
            as_of=datetime(2026, 4, 24, 17, 0, tzinfo=UTC),
        )
    )


def test_endpoint_returns_full_universe_when_no_filters(
    client: TestClient, db_session: Session
) -> None:
    """No filters → every universe ticker present in the response.

    Tickers without quotes still appear with ``"quote": null`` —
    ensures a scraper outage doesn't drop the roster.
    """
    load_universe.cache_clear()
    universe = load_universe()
    _seed_quote(db_session, "UVE", last_price=21.45)
    db_session.commit()

    response = client.get("/api/v1/quotes/hurricane-universe")
    assert response.status_code == 200
    body = response.json()
    assert "tickers" in body
    assert len(body["tickers"]) == len(universe.tickers)

    by_ticker = {row["ticker"]: row for row in body["tickers"]}
    assert by_ticker["UVE"]["quote"] is not None
    assert by_ticker["UVE"]["quote"]["last_price"] == 21.45
    # A ticker we didn't seed should still be in the payload, with null quote.
    assert by_ticker["NEE"]["quote"] is None


def test_endpoint_filters_by_sector(client: TestClient, db_session: Session) -> None:
    """``?sectors=utility`` narrows the response to utilities."""
    load_universe.cache_clear()
    response = client.get("/api/v1/quotes/hurricane-universe?sectors=utility")
    assert response.status_code == 200
    sectors = {row["sector"] for row in response.json()["tickers"]}
    assert sectors == {"utility"}


def test_endpoint_filters_by_multiple_sectors(client: TestClient, db_session: Session) -> None:
    load_universe.cache_clear()
    response = client.get("/api/v1/quotes/hurricane-universe?sectors=insurer,reinsurer")
    assert response.status_code == 200
    sectors = {row["sector"] for row in response.json()["tickers"]}
    assert sectors == {"insurer", "reinsurer"}


def test_endpoint_rejects_unknown_sector(client: TestClient) -> None:
    """A typo'd sector should 400, not silently return everything.

    The Panel 2 UI sends a known set of sectors; if a query arrives
    with an unknown one, it almost certainly indicates a UI bug —
    failing loud is the right call.
    """
    load_universe.cache_clear()
    response = client.get("/api/v1/quotes/hurricane-universe?sectors=insure")
    assert response.status_code == 400
    assert "Unknown sector" in response.json()["detail"]


def test_endpoint_filters_by_states(client: TestClient, db_session: Session) -> None:
    """``?states=FL`` returns only tickers whose key_states intersect FL.

    Reinsurers (empty key_states) must be absent — global books, no
    per-state precision.
    """
    load_universe.cache_clear()
    response = client.get("/api/v1/quotes/hurricane-universe?states=FL")
    assert response.status_code == 200
    rows = response.json()["tickers"]
    assert all("FL" in row["key_states"] for row in rows)
    assert all(row["sector"] != "reinsurer" for row in rows)


def test_endpoint_combines_sector_and_state_filters(
    client: TestClient, db_session: Session
) -> None:
    """Sector + state intersect — narrowest filter wins."""
    load_universe.cache_clear()
    response = client.get("/api/v1/quotes/hurricane-universe?sectors=utility&states=FL")
    assert response.status_code == 200
    rows = response.json()["tickers"]
    assert all(row["sector"] == "utility" for row in rows)
    assert all("FL" in row["key_states"] for row in rows)
    # The bundled universe has NEE (NextEra/FPL) at minimum.
    tickers = {row["ticker"] for row in rows}
    assert "NEE" in tickers
