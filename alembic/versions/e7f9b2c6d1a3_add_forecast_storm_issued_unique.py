"""add (storm_id, issued_at) unique constraint to forecasts

Revision ID: e7f9b2c6d1a3
Revises: c1e2a8f3d4b5
Create Date: 2026-04-24 14:00:00.000000

Day 10 introduces an UPSERT on ``forecasts`` keyed by ``(storm_id,
issued_at)`` — one row per storm per NHC advisory. The table originally
landed in 08a85c4 without any uniqueness guarantee; this migration adds
the DB-level constraint so concurrent ingest ticks can't race in a
duplicate row if the pre-insert SELECT check ever loses a tie.

Hand-written (same reason as c1e2a8f3d4b5): the sandbox can't boot
Python 3.11 to autogenerate. Uses ``batch_alter_table`` so SQLite's
lack of ALTER TABLE ADD CONSTRAINT gets handled via the rebuild-and-
copy dance Alembic does behind the scenes.

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7f9b2c6d1a3"
down_revision: str | Sequence[str] | None = "c1e2a8f3d4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("forecasts", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_forecasts_storm_issued",
            ["storm_id", "issued_at"],
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("forecasts", schema=None) as batch_op:
        batch_op.drop_constraint("uq_forecasts_storm_issued", type_="unique")
