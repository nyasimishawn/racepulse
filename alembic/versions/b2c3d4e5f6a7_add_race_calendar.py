"""Independent race calendar with audited schedule revisions.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""

from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "calendar_weekends",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("event_name", sa.String(150), nullable=False),
        sa.Column(
            "meeting_id", sa.Uuid(), sa.ForeignKey("meetings.id"), unique=True
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schedule", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_calendar_weekends_year", "calendar_weekends", ["year"])
    op.create_table(
        "calendar_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "weekend_id",
            sa.Uuid(),
            sa.ForeignKey("calendar_weekends.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schedule", sa.JSON(), nullable=False),
        sa.Column("changed_by", sa.String(255), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_calendar_revisions_weekend_id",
        "calendar_revisions",
        ["weekend_id"],
    )


def downgrade():
    op.drop_table("calendar_revisions")
    op.drop_table("calendar_weekends")
