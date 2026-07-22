from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WeatherSample(Base):
    __tablename__ = "weather_samples"
    __table_args__ = (
        UniqueConstraint(
            "race_session_id",
            "source",
            "session_time_ms",
            name="uq_weather_samples_session_source_time",
        ),
        Index(
            "idx_weather_samples_session_time",
            "race_session_id",
            "session_time_ms",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)

    race_session_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("race_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )

    source: Mapped[str] = mapped_column(String(20), nullable=False)
    session_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    air_temperature_c: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2),
        nullable=True,
    )
    track_temperature_c: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2),
        nullable=True,
    )
    humidity_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2),
        nullable=True,
    )
    pressure_hpa: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 2),
        nullable=True,
    )
    rainfall: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    wind_speed_mps: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 3),
        nullable=True,
    )
    wind_direction_deg: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )