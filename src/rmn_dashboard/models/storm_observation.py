"""StormObservation — NHC CurrentStorms.json snapshot, one row per poll per storm.

Complements ``Storm`` (identity + lifetime summary, UPSERT) and ``Forecast``
(per-advisory forecast *products* — cone, 5-day track points, discussion).
This table is the per-tick *observation*: where a storm is right now, how
fast it's moving, how strong it is, as of the moment we polled NHC.

Keeping history (one row per poll) rather than UPSERTing buys us movement
sparklines, intensification alerts, and timeline panels in later weeks
without a second pipeline. Cost is ~200 bytes per active storm per tick —
negligible even at peak Atlantic season.

Natural key is ``(storm_id, observation_time)`` — ``observation_time`` is
NHC's own ``lastUpdate`` field, *not* our wall clock, so repeat polls of
the same advisory dedupe cleanly. ``ingested_at`` is our wall clock, kept
purely for latency debugging.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rmn_dashboard.models.base import Base
from rmn_dashboard.models.storm import Storm


class StormObservation(Base):
    """One snapshot of NHC's CurrentStorms.json for a single storm."""

    __tablename__ = "storm_observations"

    id: Mapped[int] = mapped_column(primary_key=True)

    storm_id: Mapped[int] = mapped_column(
        ForeignKey("storms.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # NHC "binNumber" — e.g. "AT1", "EP2". Stable for the storm's lifetime;
    # useful for keyed lookups during the Invest-→-named-storm rename dance.
    bin_number: Mapped[str | None] = mapped_column(String(8))

    # NHC classification code, verbatim: HU, TS, TD, STD, STS, PTC, TY, PC.
    # The human-readable expansion ("Hurricane", "Tropical Storm"…) lives on
    # ``Storm.storm_type`` — stored there because it changes rarely and is
    # what the dashboard displays.
    classification: Mapped[str] = mapped_column(String(4), nullable=False)

    # Intensity in knots (NHC's unit — do NOT convert on ingest).
    intensity_kt: Mapped[int] = mapped_column(Integer, nullable=False)

    # Minimum central pressure in millibars. Nullable because early-formation
    # storms and some very-remote depressions lack a pressure estimate.
    pressure_mb: Mapped[int | None] = mapped_column(Integer)

    # Position in decimal degrees. NHC publishes both display strings
    # ("22.9N"/"79.9W") and signed decimals (latitude_numeric/longitude_numeric);
    # we keep the decimals only — display strings are trivial to re-render.
    latitude_deg: Mapped[float] = mapped_column(Float, nullable=False)
    longitude_deg: Mapped[float] = mapped_column(Float, nullable=False)

    # Storm motion. Direction = compass degrees from true north.
    # **NHC reports movement speed in MPH, NOT knots.** ``intensity_kt``
    # uses knots. Mixing the two silently produces plausible-looking
    # nonsense — unit mismatch confirmed against the authoritative NHC
    # Tropical Cyclone Status JSON File Reference (Greenlaw, 2019-04).
    # Both nullable: stationary storms have no direction, and brief
    # sub-advisory windows sometimes omit motion entirely.
    movement_dir_deg: Mapped[int | None] = mapped_column(Integer)
    movement_speed_mph: Mapped[int | None] = mapped_column(Integer)

    # NHC's own "lastUpdate" timestamp — the authoritative moment the
    # observation describes. Part of the natural key so repeat polls of
    # the same advisory dedupe cleanly.
    observation_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    # Opaque blob of advisory sub-product URLs from CurrentStorms.json
    # (forecastTrack, trackCone, publicAdvisory, forecastDiscussion, etc).
    # Stored verbatim — Day 10 will consume ``forecastTrack.zipFile`` to
    # populate ``Forecast.forecast_5day_points``; other sub-products wait
    # until their respective panels come online.
    advisory_urls: Mapped[dict | None] = mapped_column(JSON)

    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    storm: Mapped[Storm] = relationship(Storm, lazy="joined")

    __table_args__ = (
        UniqueConstraint(
            "storm_id",
            "observation_time",
            name="uq_storm_observation_storm_time",
        ),
        Index(
            "ix_storm_observations_storm_time",
            "storm_id",
            "observation_time",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<StormObservation storm_id={self.storm_id} "
            f"time={self.observation_time.isoformat()} "
            f"{self.classification} {self.intensity_kt}kt>"
        )
