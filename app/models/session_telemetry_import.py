from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SessionTelemetryImportStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class SessionTelemetryDriverStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    READY = "READY"
    PARTIAL = "PARTIAL"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class SessionTelemetryImport(Base):
    __tablename__ = "session_telemetry_imports"
    __table_args__ = (
        Index(
            "idx_session_telemetry_imports_session_status",
            "race_session_id",
            "status",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_session_telemetry_imports_idempotency_key",
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

    status: Mapped[SessionTelemetryImportStatus] = mapped_column(
        SqlEnum(
            SessionTelemetryImportStatus,
            name="session_telemetry_import_status",
        ),
        nullable=False,
        default=SessionTelemetryImportStatus.PENDING,
    )

    requested_driver_numbers: Mapped[list[str] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    max_laps_per_driver: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    clean_laps_only: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    driver_results: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
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

    laps_requested: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    laps_processed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    laps_with_telemetry: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    laps_unavailable: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    laps_failed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    points_written: Mapped[int] = mapped_column(
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
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    durable_job_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("durable_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
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
