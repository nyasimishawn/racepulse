"""Add structured Paddock explainers, tyres and driver-market context."""

from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "editorial_updates", sa.Column("context", sa.JSON(), nullable=True)
    )
    op.add_column(
        "calendar_weekends",
        sa.Column("sync_key", sa.String(500), nullable=True),
    )
    op.create_unique_constraint(
        "uq_calendar_sync_key", "calendar_weekends", ["sync_key"]
    )
    op.add_column(
        "calendar_weekends",
        sa.Column(
            "sync_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_table(
        "calendar_sync_state",
        sa.Column("year", sa.Integer(), primary_key=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("calendar_sync_state")
    op.drop_column("calendar_weekends", "sync_enabled")
    op.drop_constraint(
        "uq_calendar_sync_key", "calendar_weekends", type_="unique"
    )
    op.drop_column("calendar_weekends", "sync_key")
    op.drop_column("editorial_updates", "context")
