from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.notifications.types import NotificationEvent
from src.schemas.enums import BookingStatus, Timezone


class WorkerRemindersAliasTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_reminder_uses_master_alias(self) -> None:
        from src.workers import reminders as r

        booking = SimpleNamespace(
            id=1,
            status=BookingStatus.CONFIRMED,
            start_at=datetime.now(UTC) + timedelta(days=1),
            duration_min=60,
            master=SimpleNamespace(id=10, name="Profile", notify_clients=True),
            client=SimpleNamespace(
                id=1,
                telegram_id=100,
                timezone=Timezone.EUROPE_MINSK,
                notifications_enabled=True,
            ),
            master_id=10,
            client_id=1,
        )

        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True))

        with patch.object(r, "_resolve_display_names", AsyncMock(return_value=("Alias", None))):
            await r._send_client_reminder(
                notifier=notifier,
                event=NotificationEvent.REMINDER_2H,
                booking=booking,
                now_utc=datetime.now(UTC),
                plan_is_pro=False,
            )

        args, _kwargs = notifier.maybe_send.await_args
        request = args[0]
        self.assertEqual(request.context.master_name, "Alias")
