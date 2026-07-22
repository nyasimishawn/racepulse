from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RaceControlEvent(Base):
    __tablename__ = "race_control_events"
    __table_args__ = (
        Index(
            "idx_race_control_events_session_time",
            "race_session_id",
            "session_time_ms",
        ),
        Index(
            "idx_race_control_events_session_occurred_at",
            "race_session_id",
            "occurred_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)

    race_session_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("race_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )

    source: Mapped[str] = mapped_column(String(20), nullable=False)

    # FastF1 gives this as an absolute UTC timestamp.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Filled only when imported laps provide a reliable session clock anchor.
    session_time_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    flag: Mapped[str | None] = mapped_column(String(40), nullable=True)
    scope: Mapped[str | None] = mapped_column(String(40), nullable=True)

    sector_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    driver_number: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
    )
    lap_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )