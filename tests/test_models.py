"""Model smoke tests — proves the ORM wiring holds end to end."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from rmn_dashboard.models import CatLoss


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
