"""Storm — Atlantic hurricane / tropical system records."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from rmn_dashboard.models.base import Base


class Storm(Base):
    """A single Atlantic basin storm/tropical system.

    Identity comes from the NHC identifier (e.g. ``AL092024``). The record is
    updated in place as NHC issues new advisories — this table is one row per
    storm, not one row per advisory. Advisory-level detail lives in Forecast.
    """

    __tablename__ = "storms"

    id: Mapped[int] = mapped_column(primary_key=True)

    # NHC identifier — the natural key for an Atlantic storm
    nhc_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    season_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Classification + intensity
    storm_type: Mapped[str | None] = mapped_column(String(40))  # e.g. "Hurricane", "Tropical Storm"
    max_wind_kt: Mapped[int | None] = mapped_column(Integer)
    min_pressure_mb: Mapped[int | None] = mapped_column(Integer)

    # Lifecycle
    genesis_date: Mapped[date | None] = mapped_column(Date)
    dissipation_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(
        String(40)
    )  # "active", "post-tropical", "dissipated"

    # Geospatial — stored as GeoJSON blobs; we only need to render them, not query them
    track_geojson: Mapped[dict | None] = mapped_column(JSON)
    landfall_locations: Mapped[list | None] = mapped_column(JSON)

    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (UniqueConstraint("nhc_id", name="uq_storms_nhc_id"),)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Storm {self.nhc_id} {self.name} ({self.season_year})>"
