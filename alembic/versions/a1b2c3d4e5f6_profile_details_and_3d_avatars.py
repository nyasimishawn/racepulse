"""Add short bios, structured details and 3D avatars to public profiles.

Revision ID: a1b2c3d4e5f6
Revises: f9d0e1a2b3c4
"""

from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "f9d0e1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("driver_profiles", "team_profiles"):
        op.add_column(table, sa.Column("short_bio", sa.String(500)))
        op.add_column(table, sa.Column("avatar", sa.JSON()))
        op.add_column(table, sa.Column("details", sa.JSON()))


def downgrade() -> None:
    for table in ("team_profiles", "driver_profiles"):
        op.drop_column(table, "details")
        op.drop_column(table, "avatar")
        op.drop_column(table, "short_bio")
