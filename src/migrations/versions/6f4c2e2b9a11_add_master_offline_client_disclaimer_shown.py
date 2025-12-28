"""add master offline_client_disclaimer_shown flag

Revision ID: 6f4c2e2b9a11
Revises: 9c8b1f7a4b2c
Create Date: 2025-12-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "6f4c2e2b9a11"
down_revision = "9c8b1f7a4b2c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "masters",
        sa.Column(
            "offline_client_disclaimer_shown",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("masters", "offline_client_disclaimer_shown")
