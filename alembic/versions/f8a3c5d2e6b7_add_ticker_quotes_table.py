"""add ticker_quotes table

Revision ID: f8a3c5d2e6b7
Revises: e7f9b2c6d1a3
Create Date: 2026-04-24 22:00:00.000000

Day 13b introduces a new TickerQuote model — one snapshot per ticker
per scrape run, populated by the yfinance ingest task. This migration
creates the backing table so the scheduler has somewhere to write in
prod (Render Postgres).

Hand-written for the same reason as recent siblings: the sandbox can't
boot Python 3.11 to autogenerate. Schema mirrors
``rmn_dashboard.models.ticker_quote.TickerQuote`` exactly — keep them
in sync if either side changes.

The table is snapshot-shaped (UniqueConstraint(ticker, as_of)) like
prediction_markets — every scrape inserts; the read-side service
deduplicates back to "latest per ticker."

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8a3c5d2e6b7"
down_revision: str | Sequence[str] | None = "e7f9b2c6d1a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ticker_quotes table with snapshot constraint + indexes."""
    op.create_table(
        "ticker_quotes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column("last_price", sa.Float(), nullable=False),
        sa.Column("prior_close", sa.Float(), nullable=True),
        sa.Column("change_amount", sa.Float(), nullable=True),
        sa.Column("change_percent", sa.Float(), nullable=True),
        sa.Column(
            "currency",
            sa.String(length=3),
            nullable=False,
            server_default=sa.text("'USD'"),
        ),
        sa.Column("volume", sa.Integer(), nullable=True),
        sa.Column("market_cap", sa.Float(), nullable=True),
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'yfinance'"),
        ),
        sa.Column(
            "as_of",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker", "as_of", name="uq_ticker_quote_snapshot"),
    )
    # Indexes via batch_alter_table for SQLite's no-ADD-INDEX-on-create
    # behavior (matches the carrier_exposures pattern in 08a85c4).
    with op.batch_alter_table("ticker_quotes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ticker_quotes_ticker"), ["ticker"], unique=False)
        batch_op.create_index(batch_op.f("ix_ticker_quotes_as_of"), ["as_of"], unique=False)
        # Composite index speeds up the dedup join in
        # services/equity_quotes.latest_universe_quotes.
        batch_op.create_index("ix_ticker_quotes_ticker_as_of", ["ticker", "as_of"], unique=False)


def downgrade() -> None:
    """Drop the ticker_quotes table and its indexes."""
    with op.batch_alter_table("ticker_quotes", schema=None) as batch_op:
        batch_op.drop_index("ix_ticker_quotes_ticker_as_of")
        batch_op.drop_index(batch_op.f("ix_ticker_quotes_as_of"))
        batch_op.drop_index(batch_op.f("ix_ticker_quotes_ticker"))
    op.drop_table("ticker_quotes")
