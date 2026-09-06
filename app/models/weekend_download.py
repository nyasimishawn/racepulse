from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    JSON,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user_profile import utc_now


class WeekendDownload(Base):
    """One shared, resumable download for a provider's race weekend."""

    __tablename__ = "weekend_downloads"
    __table_args__ = (
        UniqueConstraint("year", "round_number", name="uq_weekend_year_round"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    year: Mapped[int] = mapped_column(nullable=False)
    round_number: Mapped[int] = mapped_column(nullable=False)
    event_name: Mapped[str] = mapped_column(String(150), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    meeting_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("meetings.id", ondelete="SET NULL"),
    )
    durable_job_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("durable_jobs.id", ondelete="SET NULL"),
        index=True,
    )
    # Each stage checkpoint is committed after its normalized data is saved.
    sessions: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
