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
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Lap(Base):
    __tablename__ = "laps"
    __table_args__ = (
        UniqueConstraint(
            "race_session_id",
            "driver_id",
            "lap_number",
            name="uq_laps_session_driver_lap",
        ),
        Index(
            "idx_laps_session_driver",
            "race_session_id",
            "driver_id",
        ),
        Index(
            "idx_laps_session_lap_number",
            "race_session_id",
            "lap_number",
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

    lap_number: Mapped[int] = mapped_column(Integer, nullable=False)

    lap_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    lap_start_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lap_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    sector_1_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sector_2_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sector_3_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    speed_i1: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 3),
        nullable=True,
    )
    speed_i2: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 3),
        nullable=True,
    )
    speed_fl: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 3),
        nullable=True,
    )
    speed_st: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 3),
        nullable=True,
    )

    stint: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compound: Mapped[str | None] = mapped_column(String(30), nullable=True)
    tyre_life: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 2),
        nullable=True,
    )
    fresh_tyre: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    pit_in_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pit_out_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    track_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_personal_best: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_accurate: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    deleted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    deleted_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    fastf1_generated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )