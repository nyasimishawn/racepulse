from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum as SqlEnum,
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
from app.models.session_map_import import SessionMapDriverStatus


class SessionMapImportDriver(Base):
    __tablename__ = "session_map_import_drivers"
    __table_args__ = (
        UniqueConstraint(
            "map_import_id",
            "driver_id",
            name="uq_session_map_import_driver",
        ),
        Index(
            "idx_session_map_import_drivers_job_status",
            "map_import_id",
            "status",
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

    driver_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("drivers.id", ondelete="CASCADE"),
        nullable=False,
    )

    status: Mapped[SessionMapDriverStatus] = mapped_column(
        SqlEnum(
            SessionMapDriverStatus,
            name="session_map_driver_status",
        ),
        nullable=False,
        default=SessionMapDriverStatus.PENDING,
    )

    raw_samples_seen: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    valid_samples: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    samples_written: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    first_sample_session_time_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    last_sample_session_time_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    largest_gap_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    coverage_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )