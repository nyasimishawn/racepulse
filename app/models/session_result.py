from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SessionResult(Base):
    __tablename__ = "session_results"
    __table_args__ = (
        UniqueConstraint(
            "race_session_id",
            "driver_id",
            name="uq_session_results_session_driver",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)

    race_session_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("race_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    driver_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("drivers.id"),
        nullable=False,
        index=True,
    )

    team_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("teams.id"),
        nullable=True,
        index=True,
    )

    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classified_position: Mapped[str | None] = mapped_column(String(20), nullable=True)
    grid_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    q1_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    q2_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    q3_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str | None] = mapped_column(String(255), nullable=True)
    points: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )