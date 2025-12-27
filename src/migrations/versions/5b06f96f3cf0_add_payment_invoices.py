"""add payment invoices

Revision ID: 5b06f96f3cf0
Revises: 3f2c9a8b1d7e
Create Date: 2025-12-27 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "5b06f96f3cf0"
down_revision: Union[str, Sequence[str], None] = "3f2c9a8b1d7e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_invoices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("master_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("invoice_no", sa.Integer(), nullable=False),
        sa.Column("invoice_url", sa.String(length=512), nullable=True),
        sa.Column("amount", sa.Numeric(19, 2), nullable=False),
        sa.Column("currency", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider_status_code", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["master_id"], ["masters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_no"),
    )
    op.create_index(op.f("ix_payment_invoices_invoice_no"), "payment_invoices", ["invoice_no"], unique=True)
    op.create_index(op.f("ix_payment_invoices_master_id"), "payment_invoices", ["master_id"], unique=False)
    op.create_index(
        "ix_payment_invoices_master_status_created",
        "payment_invoices",
        ["master_id", "status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_payment_invoices_master_status_created", table_name="payment_invoices")
    op.drop_index(op.f("ix_payment_invoices_master_id"), table_name="payment_invoices")
    op.drop_index(op.f("ix_payment_invoices_invoice_no"), table_name="payment_invoices")
    op.drop_table("payment_invoices")

