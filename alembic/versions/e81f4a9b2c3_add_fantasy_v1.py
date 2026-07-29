"""add fantasy v1

Revision ID: e81f4a9b2c3
Revises: d23f9c4a1b60
Create Date: 2026-07-28

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e81f4a9b2c3"
down_revision: Union[str, Sequence[str], None] = "d23f9c4a1b60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


pick_score_status = sa.Enum(
    "PENDING",
    "SCORED",
    "NOT_SCORED",
    name="fantasy_pick_score_status",
)

resolution_status = sa.Enum(
    "PENDING",
    "RESOLVED",
    "NOT_SCORED",
    name="fantasy_question_resolution_status",
)


def upgrade() -> None:

    op.create_table(
        "fantasy_predictions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_profile_id", sa.Uuid(), nullable=False),
        sa.Column("race_session_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["race_session_id"],
            ["race_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_profile_id"],
            ["user_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_profile_id",
            "race_session_id",
            name="uq_fantasy_predictions_profile_race",
        ),
    )
    op.create_index(
        "ix_fantasy_predictions_user_profile_id",
        "fantasy_predictions",
        ["user_profile_id"],
    )
    op.create_index(
        "ix_fantasy_predictions_race_session_id",
        "fantasy_predictions",
        ["race_session_id"],
    )

    op.create_table(
        "fantasy_prediction_picks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("prediction_id", sa.Uuid(), nullable=False),
        sa.Column("question_key", sa.String(length=80), nullable=False),
        sa.Column("target_session_id", sa.Uuid(), nullable=False),
        sa.Column("driver_id", sa.Uuid(), nullable=True),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("driver_ids", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["driver_id"],
            ["drivers.id"],
        ),
        sa.ForeignKeyConstraint(
            ["prediction_id"],
            ["fantasy_predictions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_session_id"],
            ["race_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "prediction_id",
            "question_key",
            name="uq_fantasy_prediction_pick_question",
        ),
    )
    op.create_index(
        "ix_fantasy_prediction_picks_prediction_id",
        "fantasy_prediction_picks",
        ["prediction_id"],
    )
    op.create_index(
        "ix_fantasy_prediction_picks_target_session_id",
        "fantasy_prediction_picks",
        ["target_session_id"],
    )
    op.create_index(
        "ix_fantasy_prediction_picks_driver_id",
        "fantasy_prediction_picks",
        ["driver_id"],
    )
    op.create_index(
        "ix_fantasy_prediction_picks_team_id",
        "fantasy_prediction_picks",
        ["team_id"],
    )

    op.create_table(
        "fantasy_prediction_pick_scores",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pick_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            pick_score_status,
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column(
            "points_awarded",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "max_points",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "is_exact",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("breakdown", sa.JSON(), nullable=False),
        sa.Column(
            "scored_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["pick_id"],
            ["fantasy_prediction_picks.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pick_id",
            name="uq_fantasy_pick_score_pick",
        ),
    )
    op.create_index(
        "ix_fantasy_prediction_pick_scores_pick_id",
        "fantasy_prediction_pick_scores",
        ["pick_id"],
    )

    op.create_table(
        "fantasy_question_resolutions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("race_session_id", sa.Uuid(), nullable=False),
        sa.Column("target_session_id", sa.Uuid(), nullable=False),
        sa.Column("question_key", sa.String(length=80), nullable=False),
        sa.Column(
            "status",
            resolution_status,
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column(
            "resolution_source",
            sa.String(length=20),
            server_default=sa.text("'AUTOMATED'"),
            nullable=False,
        ),
        sa.Column("actual_driver_id", sa.Uuid(), nullable=True),
        sa.Column("actual_team_id", sa.Uuid(), nullable=True),
        sa.Column("actual_driver_ids", sa.JSON(), nullable=False),
        sa.Column(
            "source_reference",
            sa.String(length=1000),
            nullable=True,
        ),
        sa.Column(
            "data_quality_flags",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "resolved_by_profile_id",
            sa.Uuid(),
            nullable=True,
        ),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actual_driver_id"],
            ["drivers.id"],
        ),
        sa.ForeignKeyConstraint(
            ["actual_team_id"],
            ["teams.id"],
        ),
        sa.ForeignKeyConstraint(
            ["race_session_id"],
            ["race_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_profile_id"],
            ["user_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["target_session_id"],
            ["race_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "race_session_id",
            "question_key",
            name="uq_fantasy_resolution_race_question",
        ),
    )
    op.create_index(
        "ix_fantasy_question_resolutions_race_session_id",
        "fantasy_question_resolutions",
        ["race_session_id"],
    )

    op.create_table(
        "fantasy_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_profile_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column(
            "invite_code",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "max_members",
            sa.Integer(),
            server_default=sa.text("22"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "max_members >= 2 AND max_members <= 22",
            name="ck_fantasy_group_max_members",
        ),
        sa.ForeignKeyConstraint(
            ["owner_profile_id"],
            ["user_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "invite_code",
            name="uq_fantasy_group_invite_code",
        ),
    )
    op.create_index(
        "ix_fantasy_groups_owner_profile_id",
        "fantasy_groups",
        ["owner_profile_id"],
    )

    op.create_table(
        "fantasy_group_members",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("user_profile_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["fantasy_groups.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_profile_id"],
            ["user_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "group_id",
            "user_profile_id",
            name="uq_fantasy_group_member",
        ),
    )
    op.create_index(
        "ix_fantasy_group_members_group_id",
        "fantasy_group_members",
        ["group_id"],
    )
    op.create_index(
        "ix_fantasy_group_members_user_profile_id",
        "fantasy_group_members",
        ["user_profile_id"],
    )

    op.create_table(
        "fantasy_group_weekend_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("race_session_id", sa.Uuid(), nullable=False),
        sa.Column("user_profile_id", sa.Uuid(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column(
            "points",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "exact_podium_hits",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "exact_qualifying_hits",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "scored_question_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "finalized_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["fantasy_groups.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["race_session_id"],
            ["race_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_profile_id"],
            ["user_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "group_id",
            "race_session_id",
            "user_profile_id",
            name="uq_fantasy_group_weekend_profile",
        ),
    )
    op.create_index(
        "ix_fantasy_group_weekend_results_group_id",
        "fantasy_group_weekend_results",
        ["group_id"],
    )
    op.create_index(
        "ix_fantasy_group_weekend_results_race_session_id",
        "fantasy_group_weekend_results",
        ["race_session_id"],
    )
    op.create_index(
        "ix_fantasy_group_weekend_results_user_profile_id",
        "fantasy_group_weekend_results",
        ["user_profile_id"],
    )


def downgrade() -> None:
    op.drop_table("fantasy_group_weekend_results")
    op.drop_table("fantasy_group_members")
    op.drop_table("fantasy_groups")
    op.drop_table("fantasy_question_resolutions")
    op.drop_table("fantasy_prediction_pick_scores")
    op.drop_table("fantasy_prediction_picks")
    op.drop_table("fantasy_predictions")

    bind = op.get_bind()
    resolution_status.drop(bind, checkfirst=True)
    pick_score_status.drop(bind, checkfirst=True)