"""add audit logs

Revision ID: 3c1a7b0d2f9e
Revises: 2c7f6a9d1b0e
Create Date: 2025-12-31
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "3c1a7b0d2f9e"
down_revision: Union[str, Sequence[str], None] = "2c7f6a9d1b0e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("event", sa.String(length=128), nullable=False),
        sa.Column("actor", sa.String(length=16), nullable=True),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("master_id", sa.Integer(), nullable=True),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column("booking_id", sa.Integer(), nullable=True),
        sa.Column("invite_id", sa.Integer(), nullable=True),
        sa.Column("invoice_id", sa.Integer(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["invite_id"], ["invites.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["invoice_id"], ["payment_invoices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["master_id"], ["masters.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )

    op.create_index(op.f("ix_audit_logs_event"), "audit_logs", ["event"], unique=False)
    op.create_index("ix_audit_logs_master_occurred_at", "audit_logs", ["master_id", "occurred_at"], unique=False)
    op.create_index("ix_audit_logs_client_occurred_at", "audit_logs", ["client_id", "occurred_at"], unique=False)
    op.create_index("ix_audit_logs_event_occurred_at", "audit_logs", ["event", "occurred_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_audit_logs_event_occurred_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_client_occurred_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_master_occurred_at", table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_event"), table_name="audit_logs")
    op.drop_table("audit_logs")

