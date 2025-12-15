"""add notification settings

Revision ID: 3f2c9a8b1d7e
Revises: 7abdbed47c3a
Create Date: 2025-12-15 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3f2c9a8b1d7e"
down_revision: Union[str, Sequence[str], None] = "7abdbed47c3a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "masters",
        sa.Column(
            "notify_clients",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "clients",
        sa.Column(
            "notifications_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("clients", "notifications_enabled")
    op.drop_column("masters", "notify_clients")

