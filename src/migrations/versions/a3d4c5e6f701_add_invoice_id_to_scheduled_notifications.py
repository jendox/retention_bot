"""add invoice_id to scheduled notifications

Revision ID: a3d4c5e6f701
Revises: 4f0c2a1d9e7b
Create Date: 2025-12-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3d4c5e6f701"
down_revision = "4f0c2a1d9e7b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scheduled_notifications", sa.Column("invoice_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_scheduled_notifications_invoice_id_payment_invoices",
        "scheduled_notifications",
        "payment_invoices",
        ["invoice_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_scheduled_notifications_invoice_event",
        "scheduled_notifications",
        ["invoice_id", "event"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_scheduled_notifications_invoice_event", table_name="scheduled_notifications")
    op.drop_constraint(
        "fk_scheduled_notifications_invoice_id_payment_invoices",
        "scheduled_notifications",
        type_="foreignkey",
    )
    op.drop_column("scheduled_notifications", "invoice_id")

