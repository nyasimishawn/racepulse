from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime, ForeignKey, JSON, String, UniqueConstraint, Uuid, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RaceSession(Base):
    __tablename__ = "race_sessions"
    __table_args__ = (
        UniqueConstraint(
            "meeting_id",
            "session_identifier",
            name="uq_race_sessions_meeting_identifier",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)

    meeting_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    session_identifier: Mapped[str] = mapped_column(String(20), nullable=False)
    session_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_metadata: Mapped[dict] = mapped_column(
        JSON, default=dict, nullable=False,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
