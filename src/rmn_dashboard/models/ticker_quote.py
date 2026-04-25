"""TickerQuote — snapshot of a public-equity quote for the Panel 2 ticker."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from rmn_dashboard.models.base import Base


class TickerQuote(Base):
    """One quote snapshot for a public-equity ticker.

    Snapshot-shaped, like ``PredictionMarket`` — every scrape appends a
    fresh row per ticker, and the read-side service deduplicates back
    to "latest per ticker" when rendering. This keeps writes fast (no
    upserts to think about) and gives us a free price-history series
    for sparklines down the road.

    All quote fields are nullable except ``ticker`` and ``last_price``.
    yfinance's ``fast_info`` accessor is permissive about missing values
    (a thinly-traded reinsurer's after-hours volume can be ``None``);
    we don't want a missing market-cap to abort the whole row.

    Currency defaults to USD because the universe is US-listed by
    construction. The column is still here so a future expansion
    (e.g. London-listed Lloyd's syndicates) doesn't need a migration.

    `source` is a literal ("yfinance" today; "iex" / "polygon" if we
    swap providers later) so blended-source rows are distinguishable
    in the snapshot history.
    """

    __tablename__ = "ticker_quotes"

    id: Mapped[int] = mapped_column(primary_key=True)

    ticker: Mapped[str] = mapped_column(String(10), nullable=False, index=True)

    # Quote — the only non-null measurement; if we don't have a last
    # price the snapshot has no value, so the scraper drops the row.
    last_price: Mapped[float] = mapped_column(Float, nullable=False)

    # Reference for change computation. Sometimes missing on the first
    # day of trading after an IPO or on a thinly-traded ticker; we
    # store what we have and let the read-side handle nulls.
    prior_close: Mapped[float | None] = mapped_column(Float)

    # Pre-computed deltas — could be derived but storing them keeps the
    # service-layer query trivial and lets us audit historical reading
    # of yfinance's data as it evolved.
    change_amount: Mapped[float | None] = mapped_column(Float)
    change_percent: Mapped[float | None] = mapped_column(Float)

    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    # Volume on yfinance's last_volume can run into the hundreds of
    # millions; SQLAlchemy ``Integer`` is 32-bit on SQLite — switch to
    # BigInteger if we ever need to store true daily volume for a
    # mega-cap. ``last_volume`` from yfinance is the *latest minute's*
    # volume, so 32-bit is plenty.
    volume: Mapped[int | None] = mapped_column(Integer)

    # In USD; for a $200B+ utility this fits comfortably in a Float.
    market_cap: Mapped[float | None] = mapped_column(Float)

    source: Mapped[str] = mapped_column(String(20), nullable=False, default="yfinance")

    # When the scraper recorded this snapshot. Indexed because the
    # service layer's "latest per ticker" join orders by it.
    as_of: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint("ticker", "as_of", name="uq_ticker_quote_snapshot"),
        # Speeds up the dedup query in services/equity_quotes.py.
        Index("ix_ticker_quotes_ticker_as_of", "ticker", "as_of"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TickerQuote {self.ticker} {self.last_price} as_of={self.as_of}>"
