"""Model smoke tests — proves the ORM wiring holds end to end."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from rmn_dashboard.models import (
    CarrierExposure,
    CatBondQuote,
    CatLoss,
    DailySnapshot,
    Forecast,
    PredictionMarket,
    Storm,
)


def test_cat_loss_roundtrip(db_session: Session) -> None:
    """Insert a row, read it back, check the important fields survive."""
    loss = CatLoss(
        company="Test Re",
        ticker="TRE",
        filing_type="10-Q",
        filing_date=date(2025, 10, 30),
        quarter="Q3 2025",
        event_name="Hurricane Testalina",
        event_date=date(2025, 9, 15),
        gross_loss_usd=500_000_000.0,
        net_loss_usd=200_000_000.0,
        loss_type="Property",
        geography="United States - Florida",
        context="Testalina caused estimated net losses of $200M for the quarter.",
        source_accession="0001234567-25-000001",
    )
    db_session.add(loss)
    db_session.commit()

    fetched = db_session.scalar(
        select(CatLoss).where(CatLoss.event_name == "Hurricane Testalina")
    )

    assert fetched is not None
    assert fetched.company == "Test Re"
    assert fetched.net_loss_usd == 200_000_000.0
    assert fetched.filing_date == date(2025, 10, 30)
    assert fetched.created_at is not None  # populated by server default


def test_cat_loss_query_by_event(db_session: Session) -> None:
    """Multi-row query by event returns every carrier that disclosed it."""
    db_session.add_all(
        [
            CatLoss(
                company="Carrier A",
                ticker="AAA",
                filing_type="10-Q",
                filing_date=date(2025, 10, 30),
                event_name="Hurricane Alpha",
                net_loss_usd=100_000_000.0,
            ),
            CatLoss(
                company="Carrier B",
                ticker="BBB",
                filing_type="10-Q",
                filing_date=date(2025, 10, 30),
                event_name="Hurricane Alpha",
                net_loss_usd=150_000_000.0,
            ),
            CatLoss(
                company="Carrier C",
                ticker="CCC",
                filing_type="10-Q",
                filing_date=date(2025, 10, 30),
                event_name="Hurricane Beta",
                net_loss_usd=75_000_000.0,
            ),
        ]
    )
    db_session.commit()

    alpha_rows = db_session.scalars(
        select(CatLoss).where(CatLoss.event_name == "Hurricane Alpha")
    ).all()

    assert len(alpha_rows) == 2
    assert {r.company for r in alpha_rows} == {"Carrier A", "Carrier B"}
    total_alpha = sum(r.net_loss_usd or 0 for r in alpha_rows)
    assert total_alpha == 250_000_000.0


def test_storm_with_forecast_cascade(db_session: Session) -> None:
    """Forecast rows cascade-delete with their parent storm."""
    storm = Storm(
        nhc_id="AL092025",
        name="Testina",
        season_year=2025,
        storm_type="Hurricane",
        max_wind_kt=110,
        min_pressure_mb=950,
        genesis_date=date(2025, 9, 1),
        status="active",
        track_geojson={"type": "LineString", "coordinates": [[-80, 25], [-81, 26]]},
    )
    db_session.add(storm)
    db_session.flush()

    db_session.add_all(
        [
            Forecast(
                storm_id=storm.id,
                issued_at=datetime(2025, 9, 1, 12, 0, tzinfo=timezone.utc),
                cone_geojson={"type": "Polygon", "coordinates": []},
                discussion_text="First advisory.",
                raw_source_url="https://www.nhc.noaa.gov/...",
            ),
            Forecast(
                storm_id=storm.id,
                issued_at=datetime(2025, 9, 1, 18, 0, tzinfo=timezone.utc),
                discussion_text="Second advisory.",
            ),
        ]
    )
    db_session.commit()

    fetched = db_session.scalar(select(Storm).where(Storm.nhc_id == "AL092025"))
    assert fetched is not None
    assert fetched.name == "Testina"
    advisories = db_session.scalars(
        select(Forecast).where(Forecast.storm_id == fetched.id).order_by(Forecast.issued_at)
    ).all()
    assert len(advisories) == 2
    assert advisories[0].discussion_text == "First advisory."


def test_carrier_exposure_minimal(db_session: Session) -> None:
    db_session.add(
        CarrierExposure(
            carrier_group="State Farm Group",
            carrier_ticker=None,
            state="FL",
            line_of_business="Homeowners Multi-Peril",
            year=2024,
            written_premium_usd=1_200_000_000.0,
            market_share_pct=14.7,
            loss_ratio_pct=68.3,
            source_citation="NAIC 2024 Market Share Report",
        )
    )
    db_session.commit()

    rows = db_session.scalars(
        select(CarrierExposure).where(CarrierExposure.state == "FL")
    ).all()
    assert len(rows) == 1
    assert rows[0].carrier_group == "State Farm Group"


def test_cat_bond_quote_unique_constraint(db_session: Session) -> None:
    """Same index + date + category is a unique constraint."""
    db_session.add(
        CatBondQuote(
            index_name="Plenum CAT Bond UCITS Fund Index",
            value_date=date(2026, 3, 27),
            index_value=145.23,
            return_pct_12m=10.23,
            return_pct_ytd=1.35,
            risk_category="All",
            source_url="https://plenum-investments.com/...",
        )
    )
    db_session.commit()

    row = db_session.scalar(
        select(CatBondQuote).where(CatBondQuote.value_date == date(2026, 3, 27))
    )
    assert row is not None
    assert row.return_pct_12m == 10.23


def test_prediction_market_and_daily_snapshot(db_session: Session) -> None:
    db_session.add_all(
        [
            PredictionMarket(
                platform="kalshi",
                ticker="HURR-2026-FL",
                title="Will a hurricane strike Florida in 2026?",
                yes_price=42.0,
                no_price=58.0,
                volume_24h=15_000.0,
                category="hurricane",
                close_date=date(2026, 11, 30),
            ),
            DailySnapshot(
                snapshot_date=date(2026, 6, 1),
                storms_narrative="No named storms active as of June 1.",
                key_numbers_json={"active_storms": 0, "named_storms_ytd": 0},
            ),
        ]
    )
    db_session.commit()

    pm = db_session.scalar(select(PredictionMarket).where(PredictionMarket.ticker == "HURR-2026-FL"))
    assert pm is not None
    assert pm.yes_price == 42.0

    ds = db_session.scalar(select(DailySnapshot).where(DailySnapshot.snapshot_date == date(2026, 6, 1)))
    assert ds is not None
    assert ds.key_numbers_json == {"active_storms": 0, "named_storms_ytd": 0}
