"""merge heads

Revision ID: 9f1c2d3e4b5a
Revises: a3d4c5e6f701, 7b9f0c2a1d8e
Create Date: 2026-01-03
"""

from __future__ import annotations

from typing import Sequence


revision: str = "9f1c2d3e4b5a"
down_revision: Sequence[str] = ("a3d4c5e6f701", "7b9f0c2a1d8e")
branch_labels = None
depends_on = None


def upgrade() -> None:
    return


def downgrade() -> None:
    return

