from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CalendarWeekend(Base):
    __tablename__ = "calendar_weekends"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    year: Mapped[int] = mapped_column(Integer, index=True)
    event_name: Mapped[str] = mapped_column(String(150))
    meeting_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("meetings.id"), unique=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    schedule: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sync_key: Mapped[str | None] = mapped_column(String(500), unique=True)
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    __mapper_args__ = {"version_id_col": version}


class CalendarRevision(Base):
    __tablename__ = "calendar_revisions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    weekend_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("calendar_weekends.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    schedule: Mapped[dict] = mapped_column(JSON)
    changed_by: Mapped[str] = mapped_column(String(255))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CalendarSyncState(Base):
    __tablename__ = "calendar_sync_state"
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict] = mapped_column(JSON)
