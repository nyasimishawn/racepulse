"""add durable jobs, Fantasy V2 workflow, and curated content

Revision ID: c6a7e8f9d0a1
Revises: b4c3d91e8f72
Create Date: 2026-08-05

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c6a7e8f9d0a1"
down_revision: Union[str, Sequence[str], None] = "b4c3d91e8f72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


durable_job_type = postgresql.ENUM(
    "SESSION_IMPORT",
    "SESSION_TELEMETRY_IMPORT",
    "SESSION_MAP_IMPORT",
    "RACE_CONTEXT_IMPORT",
    "FANTASY_SCORE",
    "FANTASY_FINALIZE",
    name="durable_job_type",
    create_type=False,
)

durable_job_status = postgresql.ENUM(
    "QUEUED",
    "RUNNING",
    "RETRY_WAIT",
    "CANCEL_REQUESTED",
    "CANCELLED",
    "COMPLETED",
    "FAILED",
    name="durable_job_status",
    create_type=False,
)

fantasy_entry_status = postgresql.ENUM(
    "DRAFT",
    "IN_PROGRESS",
    "COMPLETE",
    "LOCKED",
    "SCORING",
    "SCORED",
    "FINALIZED",
    name="fantasy_entry_status",
    create_type=False,
)

fantasy_weekend_question_status = postgresql.ENUM(
    "OPEN",
    "LOCKED",
    "UNAVAILABLE",
    "RESOLVED",
    "NOT_SCORED",
    name="fantasy_weekend_question_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (
        durable_job_type,
        durable_job_status,
        fantasy_entry_status,
        fantasy_weekend_question_status,
    ):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "durable_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_type", durable_job_type, nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column(
            "idempotency_key",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column("status", durable_job_status, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("progress_percentage", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancel_requested_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "cancelled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_dispatched_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "completed_at",
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_type",
            "idempotency_key",
            name="uq_durable_jobs_type_idempotency",
        ),
    )
    op.create_index(
        "ix_durable_jobs_job_type",
        "durable_jobs",
        ["job_type"],
    )
    op.create_index(
        "ix_durable_jobs_status",
        "durable_jobs",
        ["status"],
    )
    op.create_index(
        "idx_durable_jobs_status_retry",
        "durable_jobs",
        ["status", "next_retry_at"],
    )
    op.create_index(
        "idx_durable_jobs_target",
        "durable_jobs",
        ["job_type", "target_id"],
    )

    _add_import_job_link("import_jobs", "import_jobs")
    _add_import_job_link(
        "session_telemetry_imports",
        "session_telemetry_imports",
    )
    _add_import_job_link("session_map_imports", "session_map_imports")

    op.add_column(
        "fantasy_predictions",
        sa.Column(
            "status",
            fantasy_entry_status,
            server_default=sa.text("'DRAFT'"),
            nullable=False,
        ),
    )
    for name in (
        "first_lock_at",
        "last_lock_at",
        "completed_at",
        "scored_at",
        "finalized_at",
    ):
        op.add_column(
            "fantasy_predictions",
            sa.Column(name, sa.DateTime(timezone=True), nullable=True),
        )
    op.alter_column(
        "fantasy_predictions",
        "status",
        server_default=None,
    )

    op.create_table(
        "fantasy_weekend_questions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("race_session_id", sa.Uuid(), nullable=False),
        sa.Column("question_key", sa.String(length=80), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("answer_kind", sa.String(length=20), nullable=False),
        sa.Column("target_session_id", sa.Uuid(), nullable=False),
        sa.Column(
            "target_session_name",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column("locks_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_points", sa.Integer(), nullable=False),
        sa.Column("selection_limit", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            fantasy_weekend_question_status,
            nullable=False,
        ),
        sa.Column(
            "generated_at",
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
            ["target_session_id"],
            ["race_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "race_session_id",
            "question_key",
            name="uq_fantasy_weekend_question",
        ),
    )
    op.create_index(
        "ix_fantasy_weekend_questions_race_session_id",
        "fantasy_weekend_questions",
        ["race_session_id"],
    )

    op.create_table(
        "fantasy_group_weekend_eligibilities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("race_session_id", sa.Uuid(), nullable=False),
        sa.Column("user_profile_id", sa.Uuid(), nullable=False),
        sa.Column(
            "membership_joined_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "first_question_locks_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
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
            name="uq_fantasy_group_weekend_eligibility",
        ),
    )
    op.create_index(
        "ix_fantasy_group_weekend_eligibilities_group_id",
        "fantasy_group_weekend_eligibilities",
        ["group_id"],
    )
    op.create_index(
        "ix_fantasy_group_weekend_eligibilities_race_session_id",
        "fantasy_group_weekend_eligibilities",
        ["race_session_id"],
    )
    op.create_index(
        "ix_fantasy_group_weekend_eligibilities_user_profile_id",
        "fantasy_group_weekend_eligibilities",
        ["user_profile_id"],
    )

    _create_driver_profiles()
    _create_team_profiles()
    _create_profile_notable_moments()
    _create_editorial_updates()


def downgrade() -> None:
    op.drop_table("editorial_updates")
    op.drop_table("profile_notable_moments")
    op.drop_table("team_profiles")
    op.drop_table("driver_profiles")
    op.drop_table("fantasy_group_weekend_eligibilities")
    op.drop_table("fantasy_weekend_questions")

    for name in (
        "finalized_at",
        "scored_at",
        "completed_at",
        "last_lock_at",
        "first_lock_at",
        "status",
    ):
        op.drop_column("fantasy_predictions", name)

    _drop_import_job_link("session_map_imports")
    _drop_import_job_link("session_telemetry_imports")
    _drop_import_job_link("import_jobs")

    op.drop_table("durable_jobs")

    bind = op.get_bind()
    for enum_type in (
        fantasy_weekend_question_status,
        fantasy_entry_status,
        durable_job_status,
        durable_job_type,
    ):
        enum_type.drop(bind, checkfirst=True)


def _add_import_job_link(table_name: str, constraint_prefix: str) -> None:
    op.add_column(
        table_name,
        sa.Column(
            "idempotency_key",
            sa.String(length=255),
            nullable=True,
        ),
    )
    op.add_column(
        table_name,
        sa.Column("durable_job_id", sa.Uuid(), nullable=True),
    )
    op.create_unique_constraint(
        f"uq_{constraint_prefix}_idempotency_key",
        table_name,
        ["idempotency_key"],
    )
    op.create_foreign_key(
        f"fk_{constraint_prefix}_durable_job_id",
        table_name,
        "durable_jobs",
        ["durable_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        f"ix_{constraint_prefix}_durable_job_id",
        table_name,
        ["durable_job_id"],
    )


def _drop_import_job_link(table_name: str) -> None:
    op.drop_index(
        f"ix_{table_name}_durable_job_id",
        table_name=table_name,
    )
    op.drop_constraint(
        f"fk_{table_name}_durable_job_id",
        table_name,
        type_="foreignkey",
    )
    op.drop_constraint(
        f"uq_{table_name}_idempotency_key",
        table_name,
        type_="unique",
    )
    op.drop_column(table_name, "durable_job_id")
    op.drop_column(table_name, "idempotency_key")


def _curated_common_columns() -> list[sa.Column]:
    return [
        sa.Column("source_url", sa.String(length=1000), nullable=False),
        sa.Column("publisher", sa.String(length=255), nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("confidence", sa.String(length=20), nullable=False),
        sa.Column("data_quality_flags", sa.JSON(), nullable=False),
        sa.Column("created_by_profile_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_profile_id", sa.Uuid(), nullable=True),
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
    ]


def _create_driver_profiles() -> None:
    common = _curated_common_columns()
    op.create_table(
        "driver_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("driver_id", sa.Uuid(), nullable=False),
        sa.Column("biography", sa.Text(), nullable=False),
        *common,
        sa.ForeignKeyConstraint(
            ["driver_id"],
            ["drivers.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_profile_id"],
            ["user_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_profile_id"],
            ["user_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("driver_id", name="uq_driver_profiles_driver"),
        sa.CheckConstraint(
            "confidence IN ('UNVERIFIED', 'LOW', 'MEDIUM', 'HIGH')",
            name="ck_driver_profiles_confidence",
        ),
    )
    op.create_index(
        "ix_driver_profiles_driver_id",
        "driver_profiles",
        ["driver_id"],
    )


def _create_team_profiles() -> None:
    common = _curated_common_columns()
    op.create_table(
        "team_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("biography", sa.Text(), nullable=False),
        *common,
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_profile_id"],
            ["user_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_profile_id"],
            ["user_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", name="uq_team_profiles_team"),
        sa.CheckConstraint(
            "confidence IN ('UNVERIFIED', 'LOW', 'MEDIUM', 'HIGH')",
            name="ck_team_profiles_confidence",
        ),
    )
    op.create_index(
        "ix_team_profiles_team_id",
        "team_profiles",
        ["team_id"],
    )


def _create_profile_notable_moments() -> None:
    common = _curated_common_columns()
    op.create_table(
        "profile_notable_moments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("driver_id", sa.Uuid(), nullable=True),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        *common,
        sa.ForeignKeyConstraint(
            ["driver_id"],
            ["drivers.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_profile_id"],
            ["user_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_profile_id"],
            ["user_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(driver_id IS NOT NULL AND team_id IS NULL) OR "
            "(driver_id IS NULL AND team_id IS NOT NULL)",
            name="ck_profile_notable_moments_single_subject",
        ),
        sa.CheckConstraint(
            "confidence IN ('UNVERIFIED', 'LOW', 'MEDIUM', 'HIGH')",
            name="ck_profile_notable_moments_confidence",
        ),
    )
    op.create_index(
        "idx_profile_notable_moments_driver_occurred",
        "profile_notable_moments",
        ["driver_id", "occurred_at"],
    )
    op.create_index(
        "idx_profile_notable_moments_team_occurred",
        "profile_notable_moments",
        ["team_id", "occurred_at"],
    )


def _create_editorial_updates() -> None:
    common = _curated_common_columns()
    op.create_table(
        "editorial_updates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("update_type", sa.String(length=40), nullable=False),
        sa.Column(
            "publication_status",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("meeting_id", sa.Uuid(), nullable=True),
        sa.Column("race_session_id", sa.Uuid(), nullable=True),
        sa.Column("driver_id", sa.Uuid(), nullable=True),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        *common,
        sa.ForeignKeyConstraint(
            ["meeting_id"],
            ["meetings.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["race_session_id"],
            ["race_sessions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["driver_id"],
            ["drivers.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_profile_id"],
            ["user_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_profile_id"],
            ["user_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "update_type IN ("
            "'UPGRADE', 'PENALTY', 'GRID_DROP', 'RACE_CONTROL', "
            "'FIA_UPDATE', 'TEAM_UPDATE', 'PIRELLI_UPDATE'"
            ")",
            name="ck_editorial_updates_type",
        ),
        sa.CheckConstraint(
            "publication_status IN ('DRAFT', 'PUBLISHED')",
            name="ck_editorial_updates_publication_status",
        ),
        sa.CheckConstraint(
            "confidence IN ('UNVERIFIED', 'LOW', 'MEDIUM', 'HIGH')",
            name="ck_editorial_updates_confidence",
        ),
    )
    op.create_index(
        "idx_editorial_updates_publication_published",
        "editorial_updates",
        ["publication_status", "published_at"],
    )
    op.create_index(
        "idx_editorial_updates_meeting_published",
        "editorial_updates",
        ["meeting_id", "published_at"],
    )
    op.create_index(
        "idx_editorial_updates_session_published",
        "editorial_updates",
        ["race_session_id", "published_at"],
    )
