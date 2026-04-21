"""CarrierExposure — state-level carrier market share and loss ratios.

Sourced from NAIC statutory filings (and supplemental state DOI data where
available). Used by Panel 2 (the exposure map) to answer questions like
"who writes the most homeowners premium in Florida, and what's their loss
ratio?"
"""

from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from rmn_dashboard.models.base import Base, TimestampMixin


class CarrierExposure(Base, TimestampMixin):
    """One (carrier, state, line, year) observation of exposure and losses."""

    __tablename__ = "carrier_exposures"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Carrier identity — both a group name (e.g. "State Farm Group") and an
    # optional public ticker so we can link to equity-market reactions
    carrier_group: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    carrier_ticker: Mapped[str | None] = mapped_column(String(10), index=True)

    # Geography + line of business — composite natural key
    state: Mapped[str] = mapped_column(String(2), nullable=False, index=True)  # USPS code
    line_of_business: Mapped[str] = mapped_column(String(60), nullable=False)

    # Reporting year (the year the data describes, not the filing year)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Financial metrics
    written_premium_usd: Mapped[float | None] = mapped_column(Float)
    direct_losses_usd: Mapped[float | None] = mapped_column(Float)
    market_share_pct: Mapped[float | None] = mapped_column(Float)
    loss_ratio_pct: Mapped[float | None] = mapped_column(Float)

    source_citation: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        Index(
            "ix_carrier_exposures_state_year_line",
            "state",
            "year",
            "line_of_business",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<CarrierExposure {self.carrier_group} "
            f"{self.state}/{self.line_of_business} {self.year}>"
        )
