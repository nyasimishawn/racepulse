"""add durable bulk-lap import job type

Revision ID: e8c9d0f1a2b3
Revises: d7b8c9e0f1a2
Create Date: 2026-08-05

"""

from typing import Sequence, Union

from alembic import op


revision: str = "e8c9d0f1a2b3"
down_revision: Union[str, Sequence[str], None] = "d7b8c9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE durable_job_type "
        "ADD VALUE IF NOT EXISTS 'SESSION_LAPS_IMPORT'"
    )


def downgrade() -> None:
    # PostgreSQL enum values remain after downgrade; rebuilding all dependent
    # values is not a safe automatic operation.
    pass
