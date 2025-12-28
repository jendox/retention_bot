"""add scheduled notifications outbox and attendance toggle

Revision ID: 1a2b3c4d5e6f
Revises: 0f7d3b2b5e90
Create Date: 2025-12-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "1a2b3c4d5e6f"
down_revision = "0f7d3b2b5e90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "masters",
        sa.Column(
            "notify_attendance",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )

    op.create_table(
        "scheduled_notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("recipient", sa.String(length=16), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("master_id", sa.Integer(), nullable=True),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column("booking_id", sa.Integer(), nullable=True),
        sa.Column("booking_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=True),
        sa.Column("dedup_key", sa.String(length=200), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["master_id"], ["masters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup_key", name="uq_scheduled_notifications_dedup_key"),
    )

    op.create_index(
        "ix_scheduled_notifications_status_due",
        "scheduled_notifications",
        ["status", "due_at"],
        unique=False,
    )
    op.create_index(
        "ix_scheduled_notifications_booking_event",
        "scheduled_notifications",
        ["booking_id", "event"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_scheduled_notifications_booking_event", table_name="scheduled_notifications")
    op.drop_index("ix_scheduled_notifications_status_due", table_name="scheduled_notifications")
    op.drop_table("scheduled_notifications")
    op.drop_column("masters", "notify_attendance")
