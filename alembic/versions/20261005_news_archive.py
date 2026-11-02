"""Persist sourced news metadata, archive progress and the collector lease."""

from alembic import op
import sqlalchemy as sa

revision = "20261005_news_archive"
down_revision = "20260930_trivia"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "imported_news",
        sa.Column("url_hash", sa.String(64), primary_key=True),
        sa.Column("editorial_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("source_id", sa.String(40), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["editorial_id"],
            ["editorial_updates.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_imported_news_source_id", "imported_news", ["source_id"]
    )
    op.create_table(
        "news_source_state",
        sa.Column("source_id", sa.String(40), primary_key=True),
        sa.Column("publisher", sa.String(255), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("message", sa.String(500), nullable=False),
        sa.Column("archive", sa.JSON(), nullable=False),
    )
    op.create_table(
        "news_worker_lease",
        sa.Column("name", sa.String(40), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("news_worker_lease")
    op.drop_table("news_source_state")
    op.drop_index("ix_imported_news_source_id", table_name="imported_news")
    op.drop_table("imported_news")
