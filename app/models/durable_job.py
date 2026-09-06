from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum as SqlEnum,
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


class DurableJobType(str, Enum):
    WEEKEND_IMPORT = "WEEKEND_IMPORT"
    SESSION_IMPORT = "SESSION_IMPORT"
    SESSION_LAPS_IMPORT = "SESSION_LAPS_IMPORT"
    SESSION_TELEMETRY_IMPORT = "SESSION_TELEMETRY_IMPORT"
    SESSION_MAP_IMPORT = "SESSION_MAP_IMPORT"
    RACE_CONTEXT_IMPORT = "RACE_CONTEXT_IMPORT"
    FANTASY_SCORE = "FANTASY_SCORE"
    FANTASY_FINALIZE = "FANTASY_FINALIZE"


class DurableJobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DurableJob(Base):
    """PostgreSQL source of truth for work transported through Redis Streams."""

    __tablename__ = "durable_jobs"
    __table_args__ = (
        UniqueConstraint(
            "job_type",
            "idempotency_key",
            name="uq_durable_jobs_type_idempotency",
        ),
        Index(
            "idx_durable_jobs_status_retry",
            "status",
            "next_retry_at",
        ),
        Index("idx_durable_jobs_target", "job_type", "target_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    job_type: Mapped[DurableJobType] = mapped_column(
        SqlEnum(DurableJobType, name="durable_job_type"),
        nullable=False,
        index=True,
    )
    target_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    status: Mapped[DurableJobStatus] = mapped_column(
        SqlEnum(DurableJobStatus, name="durable_job_status"),
        nullable=False,
        default=DurableJobStatus.QUEUED,
        index=True,
    )
    payload: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    result: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    progress_percentage: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    lease_owner: Mapped[str | None] = mapped_column(
        String(160),
        nullable=True,
    )
    leased_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    failure_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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
