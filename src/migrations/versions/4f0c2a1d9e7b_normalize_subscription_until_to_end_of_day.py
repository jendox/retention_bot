"""normalize subscription until to end-of-day per master timezone

Revision ID: 4f0c2a1d9e7b
Revises: 3c1a7b0d2f9e
Create Date: 2025-12-31
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op


revision: str = "4f0c2a1d9e7b"
down_revision: str | None = "3c1a7b0d2f9e"
branch_labels = None
depends_on = None


def _tz(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or "UTC"))
    except Exception:
        return ZoneInfo("UTC")


def _to_eod_utc(value: datetime, tz: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    local_day = value.astimezone(tz).date()
    local_eod = datetime.combine(local_day, time(23, 59, 59, 999999), tzinfo=tz)
    return local_eod.astimezone(UTC)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT s.master_id, s.trial_until, s.paid_until, m.timezone
            FROM subscriptions s
            JOIN masters m ON m.id = s.master_id
            WHERE s.trial_until IS NOT NULL OR s.paid_until IS NOT NULL
            """,
        ),
    ).all()

    for master_id, trial_until, paid_until, tz_name in rows:
        zone = _tz(str(tz_name) if tz_name is not None else None)
        patch: dict[str, object] = {}
        if trial_until is not None:
            patch["trial_until"] = _to_eod_utc(trial_until, zone)
        if paid_until is not None:
            patch["paid_until"] = _to_eod_utc(paid_until, zone)
        if not patch:
            continue

        bind.execute(
            sa.text(
                """
                UPDATE subscriptions
                SET trial_until = COALESCE(:trial_until, trial_until),
                    paid_until = COALESCE(:paid_until, paid_until)
                WHERE master_id = :master_id
                """,
            ),
            {"master_id": master_id, "trial_until": patch.get("trial_until"), "paid_until": patch.get("paid_until")},
        )


def downgrade() -> None:
    # No-op: we can't recover original exact timestamps.
    return

