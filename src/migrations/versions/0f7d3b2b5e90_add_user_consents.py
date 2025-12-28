"""add user consents

Revision ID: 0f7d3b2b5e90
Revises: 6f4c2e2b9a11
Create Date: 2025-12-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0f7d3b2b5e90"
down_revision = "6f4c2e2b9a11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_consents",
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("telegram_id", "role", name="pk_user_consents"),
    )
    op.create_index("ix_user_consents_telegram_id", "user_consents", ["telegram_id"])


def downgrade() -> None:
    op.drop_index("ix_user_consents_telegram_id", table_name="user_consents")
    op.drop_table("user_consents")

