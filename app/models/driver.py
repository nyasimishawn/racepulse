from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Driver(Base):
    __tablename__ = "drivers"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "source_identifier",
            name="uq_drivers_source_identifier",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)

    source: Mapped[str] = mapped_column(String(20), nullable=False)
    source_identifier: Mapped[str | None] = mapped_column(String(100), nullable=True)

    driver_number: Mapped[str] = mapped_column(String(20), nullable=False)
    abbreviation: Mapped[str | None] = mapped_column(String(10), nullable=True)

    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    country_code: Mapped[str | None] = mapped_column(String(10), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )