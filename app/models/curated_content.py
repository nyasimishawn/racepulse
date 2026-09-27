from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


CONFIDENCE_CHECK = (
    "confidence IN ('UNVERIFIED', 'LOW', 'MEDIUM', 'HIGH')"
)


class DriverProfile(Base):
    """Editor-curated driver biography, separate from imported timing."""

    __tablename__ = "driver_profiles"
    __table_args__ = (
        UniqueConstraint(
            "driver_id",
            name="uq_driver_profiles_driver",
        ),
        CheckConstraint(
            CONFIDENCE_CHECK,
            name="ck_driver_profiles_confidence",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    driver_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("drivers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    biography: Mapped[str] = mapped_column(Text, nullable=False)
    short_bio: Mapped[str | None] = mapped_column(String(500), nullable=True)
    avatar: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    source_url: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
    )
    publisher: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    confidence: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="UNVERIFIED",
    )
    data_quality_flags: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    created_by_profile_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("user_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_profile_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("user_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TeamProfile(Base):
    """Editor-curated team biography, separate from imported timing."""

    __tablename__ = "team_profiles"
    __table_args__ = (
        UniqueConstraint(
            "team_id",
            name="uq_team_profiles_team",
        ),
        CheckConstraint(
            CONFIDENCE_CHECK,
            name="ck_team_profiles_confidence",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    biography: Mapped[str] = mapped_column(Text, nullable=False)
    short_bio: Mapped[str | None] = mapped_column(String(500), nullable=True)
    avatar: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    source_url: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
    )
    publisher: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    confidence: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="UNVERIFIED",
    )
    data_quality_flags: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    created_by_profile_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("user_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_profile_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("user_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ProfileNotableMoment(Base):
    """A sourced curated milestone for exactly one driver or team."""

    __tablename__ = "profile_notable_moments"
    __table_args__ = (
        CheckConstraint(
            "(driver_id IS NOT NULL AND team_id IS NULL) OR "
            "(driver_id IS NULL AND team_id IS NOT NULL)",
            name="ck_profile_notable_moments_single_subject",
        ),
        CheckConstraint(
            CONFIDENCE_CHECK,
            name="ck_profile_notable_moments_confidence",
        ),
        Index(
            "idx_profile_notable_moments_driver_occurred",
            "driver_id",
            "occurred_at",
        ),
        Index(
            "idx_profile_notable_moments_team_occurred",
            "team_id",
            "occurred_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    driver_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("drivers.id", ondelete="CASCADE"),
        nullable=True,
    )
    team_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    source_url: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
    )
    publisher: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    confidence: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="UNVERIFIED",
    )
    data_quality_flags: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    created_by_profile_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("user_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_profile_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("user_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class EditorialUpdate(Base):
    """A sourced editorial update; it never mutates imported timing facts."""

    __tablename__ = "editorial_updates"
    context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    __table_args__ = (
        CheckConstraint(
            "update_type IN ("
            "'UPGRADE', 'PENALTY', 'GRID_DROP', 'RACE_CONTROL', "
            "'FIA_UPDATE', 'TEAM_UPDATE', 'PIRELLI_UPDATE'"
            ")",
            name="ck_editorial_updates_type",
        ),
        CheckConstraint(
            "publication_status IN ('DRAFT', 'PUBLISHED')",
            name="ck_editorial_updates_publication_status",
        ),
        CheckConstraint(
            CONFIDENCE_CHECK,
            name="ck_editorial_updates_confidence",
        ),
        Index(
            "idx_editorial_updates_publication_published",
            "publication_status",
            "published_at",
        ),
        Index(
            "idx_editorial_updates_meeting_published",
            "meeting_id",
            "published_at",
        ),
        Index(
            "idx_editorial_updates_session_published",
            "race_session_id",
            "published_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    update_type: Mapped[str] = mapped_column(String(40), nullable=False)
    publication_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="DRAFT",
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    meeting_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("meetings.id", ondelete="SET NULL"),
        nullable=True,
    )
    race_session_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("race_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    driver_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("drivers.id", ondelete="SET NULL"),
        nullable=True,
    )
    team_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
    )

    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    publisher: Mapped[str] = mapped_column(String(255), nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    confidence: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="UNVERIFIED",
    )
    data_quality_flags: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    created_by_profile_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("user_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_profile_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("user_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
