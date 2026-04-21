"""CatLoss — insurer catastrophe loss disclosures extracted from SEC filings.

Schema ported from the legacy ``cat_loss_database.py`` prototype, promoted from
raw sqlite3 into a proper SQLAlchemy 2.x typed ORM model.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from rmn_dashboard.models.base import Base, TimestampMixin


class CatLoss(Base, TimestampMixin):
    """A single catastrophe loss disclosure from an SEC filing.

    One row per (company, event, filing) triple. The same event can appear
    multiple times if multiple carriers disclose it, or if one carrier
    restates it across quarters (reserve development).
    """

    __tablename__ = "cat_losses"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Carrier identity
    company: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    ticker: Mapped[str | None] = mapped_column(String(10), index=True)

    # Filing provenance
    filing_type: Mapped[str] = mapped_column(String(10), nullable=False)  # 10-K, 10-Q, 8-K
    filing_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    quarter: Mapped[str | None] = mapped_column(String(12))  # "Q3 2024", "FY 2023"
    source_accession: Mapped[str | None] = mapped_column(String(32), index=True)

    # Event identity
    event_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    event_date: Mapped[date | None] = mapped_column(Date)

    # Financials — stored as USD; FLOAT is adequate for the MVP's precision needs
    gross_loss_usd: Mapped[float | None] = mapped_column(Float)
    net_loss_usd: Mapped[float | None] = mapped_column(Float)

    # Classification
    loss_type: Mapped[str | None] = mapped_column(String(50))  # Property, Casualty, etc.
    geography: Mapped[str | None] = mapped_column(String(200))

    # Narrative
    context: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_cat_losses_event_company", "event_name", "company"),)

    def __repr__(self) -> str:  # pragma: no cover — debug aid only
        return (
            f"<CatLoss id={self.id} {self.company} "
            f"{self.event_name} net=${self.net_loss_usd or 0:,.0f}>"
        )
