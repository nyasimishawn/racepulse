"""add session telemetry imports

Revision ID: a67c1bd8f4e2
Revises: ff583bace0fe
Create Date: 2026-07-23

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a67c1bd8f4e2"
down_revision: Union[str, Sequence[str], None] = "ff583bace0fe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "session_telemetry_imports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("race_session_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "COMPLETED",
                "PARTIAL",
                "FAILED",
                name="session_telemetry_import_status",
            ),
            nullable=False,
        ),
        sa.Column(
            "requested_driver_numbers",
            sa.JSON(),
            nullable=True,
        ),
        sa.Column(
            "max_laps_per_driver",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "clean_laps_only",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "driver_results",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "progress_percentage",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "drivers_total",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "drivers_processed",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "drivers_completed",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "drivers_partial",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "drivers_skipped",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "drivers_failed",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "laps_requested",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "laps_processed",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "laps_with_telemetry",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "laps_unavailable",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "laps_failed",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "points_written",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "current_driver_number",
            sa.String(length=20),
            nullable=True,
        ),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
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
        sa.ForeignKeyConstraint(
            ["race_session_id"],
            ["race_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "idx_session_telemetry_imports_session_status",
        "session_telemetry_imports",
        ["race_session_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_session_telemetry_imports_session_status",
        table_name="session_telemetry_imports",
    )
    op.drop_table("session_telemetry_imports")