from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TelemetryPoint(Base):
    __tablename__ = "telemetry_points"
    __table_args__ = (
        UniqueConstraint(
            "lap_id",
            "sample_index",
            name="uq_telemetry_points_lap_sample",
        ),
        Index(
            "idx_telemetry_points_lap_time",
            "lap_id",
            "relative_time_ms",
        ),
        Index(
            "idx_telemetry_points_session_driver",
            "race_session_id",
            "driver_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)

    race_session_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("race_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )

    driver_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("drivers.id"),
        nullable=False,
    )

    lap_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("laps.id", ondelete="CASCADE"),
        nullable=False,
    )

    sample_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # Relative to the start of this lap.
    relative_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    # Relative to the beginning of the overall session.
    session_time_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    sampled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    speed_kph: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 3),
        nullable=True,
    )

    rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gear: Mapped[int | None] = mapped_column(Integer, nullable=True)

    throttle_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )

    brake_applied: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )

    drs: Mapped[int | None] = mapped_column(Integer, nullable=True)

    x: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 3),
        nullable=True,
    )

    y: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 3),
        nullable=True,
    )

    z: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 3),
        nullable=True,
    )

    distance_m: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 3),
        nullable=True,
    )

    sample_source: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    is_interpolated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )