"""add user profiles

Revision ID: d23f9c4a1b60
Revises: a67c1bd8f4e2
Create Date: 2026-07-27

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d23f9c4a1b60"
down_revision: Union[str, Sequence[str], None] = "a67c1bd8f4e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "keycloak_subject",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "username",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "email",
            sa.String(length=320),
            nullable=True,
        ),
        sa.Column(
            "display_name",
            sa.String(length=120),
            nullable=True,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "keycloak_subject",
            name="uq_user_profiles_keycloak_subject",
        ),
    )


def downgrade() -> None:
    op.drop_table("user_profiles")