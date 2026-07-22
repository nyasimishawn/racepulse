from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
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


class SessionMapSample(Base):
    __tablename__ = "session_map_samples"
    __table_args__ = (
        UniqueConstraint(
            "map_import_id",
            "driver_id",
            "session_time_ms",
            name="uq_session_map_sample_time",
        ),
        Index(
            "idx_session_map_samples_import_driver_time",
            "map_import_id",
            "driver_id",
            "session_time_ms",
        ),
        Index(
            "idx_session_map_samples_session_time",
            "race_session_id",
            "session_time_ms",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )

    map_import_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("session_map_imports.id", ondelete="CASCADE"),
        nullable=False,
    )

    race_session_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("race_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )

    driver_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("drivers.id", ondelete="CASCADE"),
        nullable=False,
    )

    lap_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("laps.id", ondelete="SET NULL"),
        nullable=True,
    )

    session_time_ms: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    sampled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    x: Mapped[Decimal] = mapped_column(
        Numeric(12, 3),
        nullable=False,
    )

    y: Mapped[Decimal] = mapped_column(
        Numeric(12, 3),
        nullable=False,
    )

    z: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 3),
        nullable=True,
    )

    position_status: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    sample_source: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pos",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )