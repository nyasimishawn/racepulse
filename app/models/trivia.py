"""Durable solo trivia games. Private snapshots never leave the service."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utc_now():
    return datetime.now(UTC)


class TriviaGuest(Base):
    __tablename__ = "trivia_guests"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TriviaQuestion(Base):
    __tablename__ = "trivia_questions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    difficulty: Mapped[str] = mapped_column(String(8), index=True)
    version: Mapped[int] = mapped_column(default=1)
    published: Mapped[bool] = mapped_column(default=False)
    content: Mapped[dict] = mapped_column(JSON)


class TriviaRace(Base):
    __tablename__ = "trivia_races"
    __table_args__ = (
        UniqueConstraint("owner", "start_key", name="uq_trivia_start"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner: Mapped[str] = mapped_column(String(320), index=True)
    start_key: Mapped[UUID] = mapped_column()
    difficulty: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(12), default="racing")
    revision: Mapped[int] = mapped_column(default=0)
    # Includes rules, question versions and bot moves fixed at race creation.
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class TriviaAnswer(Base):
    __tablename__ = "trivia_answers"
    __table_args__ = (
        UniqueConstraint("race_id", "assignment_id", name="uq_trivia_answer"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    race_id: Mapped[UUID] = mapped_column(
        ForeignKey("trivia_races.id", ondelete="CASCADE"), index=True
    )
    assignment_id: Mapped[UUID] = mapped_column()
    option_id: Mapped[str] = mapped_column(String(36))
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
