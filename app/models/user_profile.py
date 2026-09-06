from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, JSON, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class UserProfile(Base):
    __tablename__ = "user_profiles"
    __table_args__ = (
        UniqueConstraint(
            "keycloak_subject",
            name="uq_user_profiles_keycloak_subject",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )

    # Keycloak remains the source of truth for these identity fields. The
    # database stores a query-friendly snapshot for RacePulse domain features.
    keycloak_subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    username: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    email: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
        index=True,
    )
    email_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    keycloak_roles: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    keycloak_client_roles: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False,
    )
    keycloak_groups: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False,
    )
    given_name: Mapped[str | None] = mapped_column(String(255))
    family_name: Mapped[str | None] = mapped_column(String(255))
    identity_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    # This is a RacePulse-owned presentation field. It is deliberately not
    # overwritten when a user later changes a Keycloak username or email.
    display_name: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )

    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
