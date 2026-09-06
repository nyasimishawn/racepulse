"""expand user profiles for Keycloak identity synchronization

Revision ID: b4c3d91e8f72
Revises: e81f4a9b2c3
Create Date: 2026-07-30

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4c3d91e8f72"
down_revision: Union[str, Sequence[str], None] = "e81f4a9b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column(
            "email_verified",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "user_profiles",
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )
    op.add_column(
        "user_profiles",
        sa.Column(
            "keycloak_roles",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )
    op.add_column(
        "user_profiles",
        sa.Column(
            "identity_synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_user_profiles_username",
        "user_profiles",
        ["username"],
    )
    op.create_index(
        "ix_user_profiles_email",
        "user_profiles",
        ["email"],
    )

    op.alter_column(
        "user_profiles",
        "email_verified",
        server_default=None,
    )
    op.alter_column(
        "user_profiles",
        "is_active",
        server_default=None,
    )
    op.alter_column(
        "user_profiles",
        "keycloak_roles",
        server_default=None,
    )
    op.alter_column(
        "user_profiles",
        "identity_synced_at",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_index("ix_user_profiles_email", table_name="user_profiles")
    op.drop_index("ix_user_profiles_username", table_name="user_profiles")
    op.drop_column("user_profiles", "identity_synced_at")
    op.drop_column("user_profiles", "keycloak_roles")
    op.drop_column("user_profiles", "is_active")
    op.drop_column("user_profiles", "email_verified")
