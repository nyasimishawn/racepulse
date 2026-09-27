"""Persist weekend alert preferences and scheduled in-app alerts."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None

alert_type = sa.Enum(
    "SESSION_SOON", "FANTASY_DEADLINE", "SCHEDULE_CHANGE", name="alert_type"
)
alert_status = sa.Enum(
    "PENDING", "DELIVERED", "CANCELLED", name="alert_status"
)


def upgrade():
    bind = op.get_bind()
    alert_type.create(bind, checkfirst=True)
    alert_status.create(bind, checkfirst=True)
    op.create_table(
        "weekend_alert_preferences",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("user_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "weekend_id",
            sa.Uuid(),
            sa.ForeignKey("calendar_weekends.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_soon", sa.Boolean(), nullable=False),
        sa.Column("fantasy_deadline", sa.Boolean(), nullable=False),
        sa.Column("schedule_change", sa.Boolean(), nullable=False),
        sa.UniqueConstraint(
            "user_id", "weekend_id", name="uq_weekend_alert_preference"
        ),
    )
    op.create_index(
        "ix_weekend_alert_preferences_user_id",
        "weekend_alert_preferences",
        ["user_id"],
    )
    op.create_index(
        "ix_weekend_alert_preferences_weekend_id",
        "weekend_alert_preferences",
        ["weekend_id"],
    )
    op.create_table(
        "alerts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("user_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "weekend_id",
            sa.Uuid(),
            sa.ForeignKey("calendar_weekends.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "alert_type",
            postgresql.ENUM(name="alert_type", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="alert_status", create_type=False),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("session_identifier", sa.String(8)),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("body", sa.String(1000), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_alert_idempotency_key"
        ),
    )
    op.create_index("idx_alerts_due", "alerts", ["status", "scheduled_for"])
    op.create_index(
        "idx_alerts_user_feed", "alerts", ["user_id", "delivered_at"]
    )


def downgrade():
    op.drop_index("idx_alerts_user_feed", table_name="alerts")
    op.drop_index("idx_alerts_due", table_name="alerts")
    op.drop_table("alerts")
    op.drop_index(
        "ix_weekend_alert_preferences_weekend_id",
        table_name="weekend_alert_preferences",
    )
    op.drop_index(
        "ix_weekend_alert_preferences_user_id",
        table_name="weekend_alert_preferences",
    )
    op.drop_table("weekend_alert_preferences")
    alert_status.drop(op.get_bind(), checkfirst=True)
    alert_type.drop(op.get_bind(), checkfirst=True)
