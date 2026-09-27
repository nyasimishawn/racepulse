from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AlertType(str, Enum):
    SESSION_SOON = "SESSION_SOON"
    FANTASY_DEADLINE = "FANTASY_DEADLINE"
    SCHEDULE_CHANGE = "SCHEDULE_CHANGE"


class AlertStatus(str, Enum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


class WeekendAlertPreference(Base):
    __tablename__ = "weekend_alert_preferences"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "weekend_id", name="uq_weekend_alert_preference"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("user_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    weekend_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("calendar_weekends.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_soon: Mapped[bool] = mapped_column(default=False, nullable=False)
    fantasy_deadline: Mapped[bool] = mapped_column(
        default=False, nullable=False
    )
    schedule_change: Mapped[bool] = mapped_column(
        default=False, nullable=False
    )


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_alert_idempotency_key"),
        Index("idx_alerts_due", "status", "scheduled_for"),
        Index("idx_alerts_user_feed", "user_id", "delivered_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("user_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    weekend_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("calendar_weekends.id", ondelete="CASCADE"),
        nullable=False,
    )
    alert_type: Mapped[AlertType] = mapped_column(
        SqlEnum(AlertType, name="alert_type"), nullable=False
    )
    status: Mapped[AlertStatus] = mapped_column(
        SqlEnum(AlertStatus, name="alert_status"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    session_identifier: Mapped[str | None] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(String(1000), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
