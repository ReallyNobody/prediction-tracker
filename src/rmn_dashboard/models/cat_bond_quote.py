"""CatBondQuote — cat bond fund index values.

Primary source: Plenum UCITS Cat Bond Fund Index, published weekly. Swiss Re
Cat Bond Total Return Index values are stored here when publicly disclosed
(annual) — differentiated by ``index_name``.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Float, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from rmn_dashboard.models.base import Base, TimestampMixin


class CatBondQuote(Base, TimestampMixin):
    """One observation of a cat bond index on a given value date."""

    __tablename__ = "cat_bond_quotes"

    id: Mapped[int] = mapped_column(primary_key=True)

    # e.g. "Plenum CAT Bond UCITS Fund Index", "Swiss Re Cat Bond TR Index"
    index_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    value_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # Index values. Plenum publishes a level; Swiss Re publishes returns.
    # We store both and let the caller pick what to render.
    index_value: Mapped[float | None] = mapped_column(Float)
    return_pct_period: Mapped[float | None] = mapped_column(Float)
    return_pct_ytd: Mapped[float | None] = mapped_column(Float)
    return_pct_12m: Mapped[float | None] = mapped_column(Float)

    # Plenum splits the universe by risk category ("BB+ and above", etc.).
    risk_category: Mapped[str | None] = mapped_column(String(60))

    source_url: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        UniqueConstraint(
            "index_name",
            "value_date",
            "risk_category",
            name="uq_cat_bond_quote_index_date_category",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CatBondQuote {self.index_name} {self.value_date} ytd={self.return_pct_ytd}>"
