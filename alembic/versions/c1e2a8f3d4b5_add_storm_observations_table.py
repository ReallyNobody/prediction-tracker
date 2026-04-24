"""add storm_observations table

Revision ID: c1e2a8f3d4b5
Revises: 08a85c41589f
Create Date: 2026-04-24 12:00:00.000000

Adds the snapshot-per-poll table populated by the NHC active-storms ingest
(see ``rmn_dashboard.tasks.ingest_nhc``). Hand-written rather than autogen'd
because the dev sandbox couldn't boot Python 3.11 to run Alembic; the shape
mirrors what ``alembic revision --autogenerate`` produces on Chris's laptop.

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1e2a8f3d4b5"
down_revision: str | Sequence[str] | None = "08a85c41589f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "storm_observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("storm_id", sa.Integer(), nullable=False),
        sa.Column("bin_number", sa.String(length=8), nullable=True),
        sa.Column("classification", sa.String(length=4), nullable=False),
        sa.Column("intensity_kt", sa.Integer(), nullable=False),
        sa.Column("pressure_mb", sa.Integer(), nullable=True),
        sa.Column("latitude_deg", sa.Float(), nullable=False),
        sa.Column("longitude_deg", sa.Float(), nullable=False),
        sa.Column("movement_dir_deg", sa.Integer(), nullable=True),
        sa.Column("movement_speed_mph", sa.Integer(), nullable=True),
        sa.Column("observation_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("advisory_urls", sa.JSON(), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["storm_id"], ["storms.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "storm_id",
            "observation_time",
            name="uq_storm_observation_storm_time",
        ),
    )
    with op.batch_alter_table("storm_observations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_storm_observations_storm_id"),
            ["storm_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_storm_observations_observation_time"),
            ["observation_time"],
            unique=False,
        )
        batch_op.create_index(
            "ix_storm_observations_storm_time",
            ["storm_id", "observation_time"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("storm_observations", schema=None) as batch_op:
        batch_op.drop_index("ix_storm_observations_storm_time")
        batch_op.drop_index(batch_op.f("ix_storm_observations_observation_time"))
        batch_op.drop_index(batch_op.f("ix_storm_observations_storm_id"))

    op.drop_table("storm_observations")
