from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SessionMapImportStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class SessionMapDriverStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    READY = "READY"
    PARTIAL = "PARTIAL"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class SessionMapImport(Base):
    __tablename__ = "session_map_imports"
    __table_args__ = (
        Index(
            "idx_session_map_imports_session_status",
            "race_session_id",
            "status",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )

    race_session_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("race_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )

    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="FASTF1",
    )

    status: Mapped[SessionMapImportStatus] = mapped_column(
        SqlEnum(
            SessionMapImportStatus,
            name="session_map_import_status",
        ),
        nullable=False,
        default=SessionMapImportStatus.PENDING,
    )

    sample_interval_ms: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=250,
    )

    requested_driver_numbers: Mapped[list[str] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    progress_percentage: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    drivers_total: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    drivers_processed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    drivers_completed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    drivers_partial: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    drivers_skipped: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    drivers_failed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    samples_written: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    current_driver_number: Mapped[str | None] = mapped_column(
        String(20),
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