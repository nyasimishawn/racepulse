"""Download complete weekends and mirror Keycloak groups.

Revision ID: f9d0e1a2b3c4
Revises: e8c9d0f1a2b3
"""

from alembic import op
import sqlalchemy as sa


revision = "f9d0e1a2b3c4"
down_revision = "e8c9d0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE durable_job_type ADD VALUE IF NOT EXISTS 'WEEKEND_IMPORT'"
    )
    for name in ("keycloak_groups", "keycloak_client_roles"):
        op.add_column(
            "user_profiles",
            sa.Column(
                name,
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'::json"),
            ),
        )
        op.alter_column("user_profiles", name, server_default=None)
    op.add_column("user_profiles", sa.Column("given_name", sa.String(255)))
    op.add_column("user_profiles", sa.Column("family_name", sa.String(255)))
    op.add_column(
        "race_sessions",
        sa.Column(
            "source_metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )
    op.alter_column("race_sessions", "source_metadata", server_default=None)
    op.alter_column(
        "session_telemetry_imports",
        "max_laps_per_driver",
        nullable=True,
        existing_type=sa.Integer(),
    )
    op.create_table(
        "weekend_downloads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("event_name", sa.String(150), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column(
            "meeting_id",
            sa.Uuid(),
            sa.ForeignKey(
                "meetings.id",
                ondelete="SET NULL",
            ),
        ),
        sa.Column(
            "durable_job_id",
            sa.Uuid(),
            sa.ForeignKey(
                "durable_jobs.id",
                ondelete="SET NULL",
            ),
        ),
        sa.Column("sessions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "year", "round_number", name="uq_weekend_year_round"
        ),
    )
    op.create_index(
        "ix_weekend_downloads_durable_job_id",
        "weekend_downloads",
        ["durable_job_id"],
    )


def downgrade() -> None:
    op.drop_table("weekend_downloads")
    op.execute(
        "UPDATE session_telemetry_imports SET max_laps_per_driver = 6 "
        "WHERE max_laps_per_driver IS NULL"
    )
    op.alter_column(
        "session_telemetry_imports",
        "max_laps_per_driver",
        nullable=False,
        existing_type=sa.Integer(),
    )
    op.drop_column("race_sessions", "source_metadata")
    for name in (
        "family_name",
        "given_name",
        "keycloak_client_roles",
        "keycloak_groups",
    ):
        op.drop_column("user_profiles", name)
    # PostgreSQL enum values cannot be removed without rebuilding the type.
