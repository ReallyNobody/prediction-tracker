"""DailySnapshot — generated 'what changed today' summaries.

Panel 6 on the dashboard. One row per calendar day per environment; we
regenerate by deleting + re-inserting so ``snapshot_date`` is unique.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, JSON, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from rmn_dashboard.models.base import Base


class DailySnapshot(Base):
    """A single day's rollup narrative for each panel plus key numbers."""

    __tablename__ = "daily_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)

    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # Per-panel narratives (plain English, pre-rendered by the translation layer)
    storms_narrative: Mapped[str | None] = mapped_column(Text)
    exposure_narrative: Mapped[str | None] = mapped_column(Text)
    spreads_narrative: Mapped[str | None] = mapped_column(Text)
    prediction_narrative: Mapped[str | None] = mapped_column(Text)

    # Key quantitative facts for the day, as a JSON blob — frees us from
    # migrating the schema every time Panel 6's caption template evolves.
    key_numbers_json: Mapped[dict | None] = mapped_column(JSON)

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("snapshot_date", name="uq_daily_snapshot_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DailySnapshot {self.snapshot_date}>"
