"""add paid_notified_at to payment_invoices

Revision ID: 9c8b1f7a4b2c
Revises: 5b06f96f3cf0
Create Date: 2025-12-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "9c8b1f7a4b2c"
down_revision = "5b06f96f3cf0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payment_invoices", sa.Column("paid_notified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("payment_invoices", "paid_notified_at")

