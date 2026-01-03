"""Use BIGINT for audit_logs.actor_id

Revision ID: 7b9f0c2a1d8e
Revises: 4f0c2a1d9e7b
Create Date: 2026-01-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = "7b9f0c2a1d8e"
down_revision: str | None = "4f0c2a1d9e7b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Telegram user ids do not fit into int32; actor_id stores telegram ids for client/master actions.
    op.alter_column(
        "audit_logs",
        "actor_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "audit_logs",
        "actor_id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )

