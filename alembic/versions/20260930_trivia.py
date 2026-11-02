"""Durable trivia races and the sourced starter question bank."""

from alembic import op
import sqlalchemy as sa

from app.services.trivia_seed_v1 import seed_rows

revision = "20260930_trivia"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "trivia_guests",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    questions = op.create_table(
        "trivia_questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("difficulty", sa.String(8), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("published", sa.Boolean(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_trivia_questions_difficulty", "trivia_questions", ["difficulty"]
    )
    op.create_table(
        "trivia_races",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner", sa.String(320), nullable=False),
        sa.Column("start_key", sa.Uuid(), nullable=False),
        sa.Column("difficulty", sa.String(8), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner", "start_key", name="uq_trivia_start"),
    )
    op.create_index("ix_trivia_races_owner", "trivia_races", ["owner"])
    op.create_table(
        "trivia_answers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "race_id",
            sa.Uuid(),
            sa.ForeignKey("trivia_races.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("option_id", sa.String(36), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "race_id", "assignment_id", name="uq_trivia_answer"
        ),
    )
    op.create_index("ix_trivia_answers_race_id", "trivia_answers", ["race_id"])
    op.bulk_insert(questions, list(seed_rows()))


def downgrade():
    op.drop_table("trivia_answers")
    op.drop_table("trivia_races")
    op.drop_table("trivia_questions")
    op.drop_table("trivia_guests")
