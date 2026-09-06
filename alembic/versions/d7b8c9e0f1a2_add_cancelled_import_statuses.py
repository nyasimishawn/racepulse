"""add cancelled states for cooperative import cancellation

Revision ID: d7b8c9e0f1a2
Revises: c6a7e8f9d0a1
Create Date: 2026-08-05

"""

from typing import Sequence, Union

from alembic import op


revision: str = "d7b8c9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "c6a7e8f9d0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL enum values cannot be removed safely in a downgrade. These
    # statements are idempotent for databases that were initialized after the
    # model enum already contained CANCELLED.
    op.execute(
        "ALTER TYPE import_job_status ADD VALUE IF NOT EXISTS 'CANCELLED'"
    )
    op.execute(
        "ALTER TYPE session_telemetry_import_status "
        "ADD VALUE IF NOT EXISTS 'CANCELLED'"
    )
    op.execute(
        "ALTER TYPE session_map_import_status "
        "ADD VALUE IF NOT EXISTS 'CANCELLED'"
    )


def downgrade() -> None:
    # PostgreSQL does not support dropping enum values without rebuilding each
    # dependent type and table. Keep the added values on downgrade.
    pass
