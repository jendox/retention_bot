"""Add masters.onboarding_nudges_enabled

Revision ID: 2c7f6a9d1b0e
Revises: 1a2b3c4d5e6f
Create Date: 2025-12-28
"""

from alembic import op
import sqlalchemy as sa


revision = "2c7f6a9d1b0e"
down_revision = "1a2b3c4d5e6f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "masters",
        sa.Column(
            "onboarding_nudges_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("masters", "onboarding_nudges_enabled")

