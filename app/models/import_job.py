from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DataSource(str, Enum):
    FASTF1 = "FASTF1"
    OPENF1 = "OPENF1"
    DEMO = "DEMO"


class ImportJobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ImportJob(Base):
    __tablename__ = "import_jobs"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_import_jobs_idempotency_key",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)

    source: Mapped[DataSource] = mapped_column(
        SqlEnum(DataSource, name="data_source"),
        nullable=False,
    )

    year: Mapped[int] = mapped_column(nullable=False)
    event_name: Mapped[str] = mapped_column(String(150), nullable=False)
    session_type: Mapped[str] = mapped_column(String(50), nullable=False)

    status: Mapped[ImportJobStatus] = mapped_column(
        SqlEnum(ImportJobStatus, name="import_job_status"),
        nullable=False,
        default=ImportJobStatus.PENDING,
    )

    progress_percentage: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    imported_session_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("race_sessions.id"),
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
