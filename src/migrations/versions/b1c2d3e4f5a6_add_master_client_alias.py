"""add master_clients.client_alias

Revision ID: b1c2d3e4f5a6
Revises: 9f1c2d3e4b5a
Create Date: 2026-01-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b1c2d3e4f5a6"
down_revision = "9f1c2d3e4b5a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "master_clients",
        sa.Column(
            "client_alias",
            sa.String(length=255),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("master_clients", "client_alias")

