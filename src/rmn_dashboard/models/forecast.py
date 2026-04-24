"""Forecast — NHC forecast snapshots, one-to-many with Storm."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rmn_dashboard.models.base import Base
from rmn_dashboard.models.storm import Storm


class Forecast(Base):
    """One snapshot of NHC's forecast for a given storm.

    NHC issues advisories at fixed intervals (every 3–6 hours while a storm
    is active). Each advisory becomes a row here — we preserve the full
    history so Panel 6 ("what changed today") can diff against yesterday's.

    ``(storm_id, issued_at)`` is unique: one row per storm per advisory.
    The Day 10 ingest does a pre-insert SELECT for idempotency, and the
    DB-level constraint is the belt-and-suspenders layer under that.
    """

    __tablename__ = "forecasts"
    __table_args__ = (
        UniqueConstraint("storm_id", "issued_at", name="uq_forecasts_storm_issued"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    storm_id: Mapped[int] = mapped_column(
        ForeignKey("storms.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    # Spatial products from NHC
    cone_geojson: Mapped[dict | None] = mapped_column(JSON)
    wind_probability_geojson: Mapped[dict | None] = mapped_column(JSON)
    forecast_5day_points: Mapped[list | None] = mapped_column(JSON)

    # Narrative
    discussion_text: Mapped[str | None] = mapped_column(Text)
    raw_source_url: Mapped[str | None] = mapped_column(String(500))

    storm: Mapped[Storm] = relationship(Storm, lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Forecast storm_id={self.storm_id} issued={self.issued_at.isoformat()}>"
