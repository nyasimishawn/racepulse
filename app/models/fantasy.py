from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FantasyPickScoreStatus(str, Enum):
    PENDING = "PENDING"
    SCORED = "SCORED"
    NOT_SCORED = "NOT_SCORED"


class FantasyQuestionResolutionStatus(str, Enum):
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    NOT_SCORED = "NOT_SCORED"


class FantasyPrediction(Base):
    __tablename__ = "fantasy_predictions"
    __table_args__ = (
        UniqueConstraint(
            "user_profile_id",
            "race_session_id",
            name="uq_fantasy_predictions_profile_race",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    user_profile_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("user_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    race_session_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("race_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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


class FantasyPredictionPick(Base):
    __tablename__ = "fantasy_prediction_picks"
    __table_args__ = (
        UniqueConstraint(
            "prediction_id",
            "question_key",
            name="uq_fantasy_prediction_pick_question",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    prediction_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("fantasy_predictions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question_key: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
    )
    target_session_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("race_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    driver_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("drivers.id"),
        nullable=True,
        index=True,
    )
    team_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("teams.id"),
        nullable=True,
        index=True,
    )
    driver_ids: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
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


class FantasyPredictionPickScore(Base):
    __tablename__ = "fantasy_prediction_pick_scores"
    __table_args__ = (
        UniqueConstraint(
            "pick_id",
            name="uq_fantasy_pick_score_pick",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    pick_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("fantasy_prediction_picks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[FantasyPickScoreStatus] = mapped_column(
        SqlEnum(
            FantasyPickScoreStatus,
            name="fantasy_pick_score_status",
        ),
        default=FantasyPickScoreStatus.PENDING,
        nullable=False,
    )
    points_awarded: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    max_points: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    is_exact: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    breakdown: Mapped[dict[str, object]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    scored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class FantasyQuestionResolution(Base):
    __tablename__ = "fantasy_question_resolutions"
    __table_args__ = (
        UniqueConstraint(
            "race_session_id",
            "question_key",
            name="uq_fantasy_resolution_race_question",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    race_session_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("race_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_session_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("race_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_key: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
    )
    status: Mapped[FantasyQuestionResolutionStatus] = mapped_column(
        SqlEnum(
            FantasyQuestionResolutionStatus,
            name="fantasy_question_resolution_status",
        ),
        default=FantasyQuestionResolutionStatus.PENDING,
        nullable=False,
    )
    resolution_source: Mapped[str] = mapped_column(
        String(20),
        default="AUTOMATED",
        nullable=False,
    )
    actual_driver_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("drivers.id"),
        nullable=True,
    )
    actual_team_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("teams.id"),
        nullable=True,
    )
    actual_driver_ids: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    source_reference: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )
    data_quality_flags: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    resolved_by_profile_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("user_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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


class FantasyGroup(Base):
    __tablename__ = "fantasy_groups"
    __table_args__ = (
        CheckConstraint(
            "max_members >= 2 AND max_members <= 22",
            name="ck_fantasy_group_max_members",
        ),
        UniqueConstraint(
            "invite_code",
            name="uq_fantasy_group_invite_code",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    owner_profile_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("user_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
    )
    invite_code: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    max_members: Mapped[int] = mapped_column(
        Integer,
        default=22,
        nullable=False,
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


class FantasyGroupMember(Base):
    __tablename__ = "fantasy_group_members"
    __table_args__ = (
        UniqueConstraint(
            "group_id",
            "user_profile_id",
            name="uq_fantasy_group_member",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    group_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("fantasy_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_profile_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("user_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class FantasyGroupWeekendResult(Base):
    __tablename__ = "fantasy_group_weekend_results"
    __table_args__ = (
        UniqueConstraint(
            "group_id",
            "race_session_id",
            "user_profile_id",
            name="uq_fantasy_group_weekend_profile",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    group_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("fantasy_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    race_session_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("race_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_profile_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("user_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rank: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    points: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    exact_podium_hits: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    exact_qualifying_hits: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    scored_question_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    finalized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )