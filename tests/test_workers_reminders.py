from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class ReminderWorkerTests(unittest.TestCase):
    def test_due_window_offsets_by_kind_and_tick(self) -> None:
        from src.workers.reminders import REMINDERS, due_window

        now = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
        tick = timedelta(seconds=60)
        kind_24h = next(k for k in REMINDERS if k.name == "24h")

        start, end = due_window(now_utc=now, kind=kind_24h, tick=tick)

        self.assertEqual(start, now + timedelta(hours=24))
        self.assertEqual(end, now + timedelta(hours=24, seconds=60))

    def test_dedup_key_changes_on_reschedule(self) -> None:
        from src.workers.reminders import REMINDERS, dedup_key

        kind_2h = next(k for k in REMINDERS if k.name == "2h")
        booking_id = 10
        a = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
        b = datetime(2025, 1, 1, 13, 0, tzinfo=UTC)

        key_a = dedup_key(booking_id=booking_id, start_at_utc=a, kind=kind_2h)
        key_b = dedup_key(booking_id=booking_id, start_at_utc=b, kind=kind_2h)

        self.assertNotEqual(key_a, key_b)


class ReminderWorkerOutboxTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_job_master_attendance_nudge_sends_with_keyboard(self) -> None:
        from src.schemas.enums import Timezone
        from src.workers import reminders as w

        start_at = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
        booking = SimpleNamespace(
            id=7,
            master_id=1,
            start_at=start_at,
            duration_min=60,
            status=w.BookingStatus.CONFIRMED,
            attendance_outcome=w.AttendanceOutcome.UNKNOWN,
            master=SimpleNamespace(id=1, name="M", timezone=Timezone.EUROPE_MINSK, notify_attendance=True),
            client=SimpleNamespace(id=2, name="C"),
        )
        job = SimpleNamespace(
            id=1,
            event=w.NotificationEvent.MASTER_ATTENDANCE_NUDGE.value,
            recipient=w.RecipientKind.MASTER.value,
            chat_id=10,
            booking_id=7,
            booking_start_at=start_at,
        )

        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True))
        with (
            patch.object(w, "_load_booking_for_notification", AsyncMock(return_value=booking)),
            patch.object(w, "_plan_is_pro", AsyncMock(return_value=True)),
        ):
            result = await w._send_job(
                notifier=notifier,
                event=w.NotificationEvent.MASTER_ATTENDANCE_NUDGE,
                recipient=w.RecipientKind.MASTER,
                job=job,
                now_utc=datetime(2025, 1, 2, 12, 0, tzinfo=UTC),
                plan_cache={},
            )

        self.assertTrue(result)
        notifier.maybe_send.assert_awaited()
        request = notifier.maybe_send.await_args.args[0]
        self.assertIsNotNone(request.reply_markup)
        cb_data = request.reply_markup.inline_keyboard[0][0].callback_data
        self.assertTrue(cb_data.startswith("m:att_rem:attended:"))

    async def test_send_job_master_attendance_nudge_skips_when_disabled(self) -> None:
        from src.schemas.enums import Timezone
        from src.workers import reminders as w

        start_at = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
        booking = SimpleNamespace(
            id=7,
            master_id=1,
            start_at=start_at,
            duration_min=60,
            status=w.BookingStatus.CONFIRMED,
            attendance_outcome=w.AttendanceOutcome.UNKNOWN,
            master=SimpleNamespace(id=1, name="M", timezone=Timezone.EUROPE_MINSK, notify_attendance=False),
            client=SimpleNamespace(id=2, name="C"),
        )
        job = SimpleNamespace(
            id=1,
            event=w.NotificationEvent.MASTER_ATTENDANCE_NUDGE.value,
            recipient=w.RecipientKind.MASTER.value,
            chat_id=10,
            booking_id=7,
            booking_start_at=start_at,
        )

        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True))
        with (
            patch.object(w, "_load_booking_for_notification", AsyncMock(return_value=booking)),
            patch.object(w, "_plan_is_pro", AsyncMock(return_value=True)),
        ):
            result = await w._send_job(
                notifier=notifier,
                event=w.NotificationEvent.MASTER_ATTENDANCE_NUDGE,
                recipient=w.RecipientKind.MASTER,
                job=job,
                now_utc=datetime(2025, 1, 2, 12, 0, tzinfo=UTC),
                plan_cache={},
            )

        self.assertFalse(result)
        notifier.maybe_send.assert_not_awaited()
