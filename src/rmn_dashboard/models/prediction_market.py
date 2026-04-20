"""PredictionMarket — prediction market contracts (Kalshi + Polymarket)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from rmn_dashboard.models.base import Base


class PredictionMarket(Base):
    """One prediction market contract observation.

    ``platform`` + ``ticker`` is the natural key for a contract. We snapshot
    the quotes repeatedly; callers that want a time series should read the
    whole history and order by ``last_updated``.
    """

    __tablename__ = "prediction_markets"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Source identification
    platform: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # kalshi | polymarket
    ticker: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    event_ticker: Mapped[str | None] = mapped_column(String(100), index=True)

    # Presentation
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str | None] = mapped_column(String(40), index=True)  # hurricane, wildfire, etc.

    # Quotes. Prices in cents (0–100) for both platforms after normalization.
    yes_price: Mapped[float | None] = mapped_column(Float)
    no_price: Mapped[float | None] = mapped_column(Float)

    # Liquidity
    volume_24h: Mapped[float | None] = mapped_column(Float)
    volume_total: Mapped[float | None] = mapped_column(Float)
    open_interest: Mapped[float | None] = mapped_column(Float)

    close_date: Mapped[date | None] = mapped_column(Date)

    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint("platform", "ticker", "last_updated", name="uq_prediction_market_snapshot"),
        Index("ix_prediction_markets_category_close", "category", "close_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PredictionMarket {self.platform}:{self.ticker} yes={self.yes_price}>"
