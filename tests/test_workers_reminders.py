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
        from src.notifications.notifier import Notifier
        from src.notifications.policy import DefaultNotificationPolicy
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

        notifier = Notifier(bot=SimpleNamespace(), policy=DefaultNotificationPolicy())
        send_mock = AsyncMock()
        with (
            patch("src.notifications.notifier.NotificationService.send", send_mock),
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
        send_mock.assert_awaited()
        reply_markup = send_mock.await_args.kwargs["reply_markup"]
        self.assertIsNotNone(reply_markup)
        cb_data = reply_markup.inline_keyboard[0][0].callback_data
        self.assertTrue(cb_data.startswith("m:att_rem:attended:"))

    async def test_send_job_master_attendance_nudge_skips_when_disabled(self) -> None:
        from src.notifications.notifier import Notifier
        from src.notifications.policy import DefaultNotificationPolicy
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

        notifier = Notifier(bot=SimpleNamespace(), policy=DefaultNotificationPolicy())
        send_mock = AsyncMock()
        with (
            patch("src.notifications.notifier.NotificationService.send", send_mock),
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
        send_mock.assert_not_awaited()

    async def test_send_job_master_onboarding_first_client_sends_with_keyboard(self) -> None:
        from src.notifications.notifier import Notifier
        from src.notifications.policy import DefaultNotificationPolicy
        from src.workers import reminders as w

        job = SimpleNamespace(
            id=1,
            event=w.NotificationEvent.MASTER_ONBOARDING_ADD_FIRST_CLIENT.value,
            recipient=w.RecipientKind.MASTER.value,
            chat_id=10,
            master_id=1,
            booking_id=None,
            booking_start_at=None,
            sequence=2,
        )

        master = SimpleNamespace(id=1, name="M", onboarding_nudges_enabled=True)
        notifier = Notifier(bot=SimpleNamespace(), policy=DefaultNotificationPolicy())
        send_mock = AsyncMock()
        with (
            patch("src.notifications.notifier.NotificationService.send", send_mock),
            patch.object(w, "_load_master_for_notification", AsyncMock(return_value=master)),
            patch.object(w, "_plan_is_pro", AsyncMock(return_value=False)),
        ):
            result = await w._send_job(
                notifier=notifier,
                event=w.NotificationEvent.MASTER_ONBOARDING_ADD_FIRST_CLIENT,
                recipient=w.RecipientKind.MASTER,
                job=job,
                now_utc=datetime(2025, 1, 2, 12, 0, tzinfo=UTC),
                plan_cache={},
            )

        self.assertTrue(result)
        send_mock.assert_awaited()
        reply_markup = send_mock.await_args.kwargs["reply_markup"]
        self.assertIsNotNone(reply_markup)
        cb_data = reply_markup.inline_keyboard[0][0].callback_data
        self.assertTrue(cb_data.startswith("m:onb:"))

    async def test_send_job_booking_created_confirmed_sends_with_cancel_button(self) -> None:
        from src.notifications.notifier import Notifier
        from src.notifications.policy import DefaultNotificationPolicy
        from src.schemas.enums import Timezone
        from src.workers import reminders as w

        start_at = datetime(2025, 1, 10, 12, 0, tzinfo=UTC)
        booking = SimpleNamespace(
            id=7,
            master_id=1,
            start_at=start_at,
            duration_min=60,
            status=w.BookingStatus.CONFIRMED,
            master=SimpleNamespace(id=1, name="M", telegram_id=999, notify_clients=True, notify_attendance=True),
            client=SimpleNamespace(
                id=2,
                name="C",
                telegram_id=123,
                timezone=Timezone.EUROPE_MINSK,
                notifications_enabled=True,
            ),
        )
        job = SimpleNamespace(
            id=1,
            event=w.NotificationEvent.BOOKING_CREATED_CONFIRMED.value,
            recipient=w.RecipientKind.CLIENT.value,
            chat_id=123,
            booking_id=7,
            booking_start_at=start_at,
        )

        notifier = Notifier(bot=SimpleNamespace(), policy=DefaultNotificationPolicy())
        send_mock = AsyncMock()
        with (
            patch("src.notifications.notifier.NotificationService.send", send_mock),
            patch.object(w, "_load_booking_for_notification", AsyncMock(return_value=booking)),
            patch.object(w, "_plan_is_pro", AsyncMock(return_value=True)),
        ):
            result = await w._send_job(
                notifier=notifier,
                event=w.NotificationEvent.BOOKING_CREATED_CONFIRMED,
                recipient=w.RecipientKind.CLIENT,
                job=job,
                now_utc=datetime(2025, 1, 9, 12, 0, tzinfo=UTC),
                plan_cache={},
            )

        self.assertTrue(result)
        send_mock.assert_awaited()
        reply_markup = send_mock.await_args.kwargs["reply_markup"]
        self.assertIsNotNone(reply_markup)
        callbacks = [b.callback_data for row in reply_markup.inline_keyboard for b in row if b.callback_data]
        self.assertTrue(any(cb.startswith("c:bookings:cancel_ntf:") for cb in callbacks))

    async def test_send_job_pro_invoice_reminder_skips_for_pro(self) -> None:
        from src.notifications.notifier import Notifier
        from src.notifications.policy import DefaultNotificationPolicy
        from src.workers import reminders as w

        job = SimpleNamespace(
            id=1,
            event=w.NotificationEvent.PRO_INVOICE_REMINDER.value,
            recipient=w.RecipientKind.MASTER.value,
            chat_id=10,
            master_id=1,
            invoice_id=2,
            booking_id=None,
            booking_start_at=None,
        )
        master = SimpleNamespace(id=1, name="M")
        invoice = SimpleNamespace(id=2, master_id=1, status="waiting", expires_at=None)

        notifier = Notifier(bot=SimpleNamespace(), policy=DefaultNotificationPolicy())
        send_mock = AsyncMock()
        with (
            patch("src.notifications.notifier.NotificationService.send", send_mock),
            patch.object(w, "_load_master_for_notification", AsyncMock(return_value=master)),
            patch.object(w, "_plan_is_pro", AsyncMock(return_value=True)),
            patch.object(w, "_load_invoice_for_notification", AsyncMock(return_value=invoice)),
            patch.object(w, "_load_latest_waiting_invoice_for_master", AsyncMock(return_value=invoice)),
        ):
            result = await w._send_job(
                notifier=notifier,
                event=w.NotificationEvent.PRO_INVOICE_REMINDER,
                recipient=w.RecipientKind.MASTER,
                job=job,
                now_utc=datetime(2025, 1, 2, 12, 0, tzinfo=UTC),
                plan_cache={},
            )

        self.assertFalse(result)
        send_mock.assert_not_awaited()

    async def test_send_job_pro_invoice_reminder_sends_for_free(self) -> None:
        from src.notifications.notifier import Notifier
        from src.notifications.policy import DefaultNotificationPolicy
        from src.workers import reminders as w

        job = SimpleNamespace(
            id=1,
            event=w.NotificationEvent.PRO_INVOICE_REMINDER.value,
            recipient=w.RecipientKind.MASTER.value,
            chat_id=10,
            master_id=1,
            invoice_id=2,
            booking_id=None,
            booking_start_at=None,
        )
        master = SimpleNamespace(id=1, name="M")
        invoice = SimpleNamespace(id=2, master_id=1, status="waiting", expires_at=None)

        notifier = Notifier(bot=SimpleNamespace(), policy=DefaultNotificationPolicy())
        send_mock = AsyncMock()
        with (
            patch("src.notifications.notifier.NotificationService.send", send_mock),
            patch.object(w, "_load_master_for_notification", AsyncMock(return_value=master)),
            patch.object(w, "_plan_is_pro", AsyncMock(return_value=False)),
            patch.object(w, "_load_invoice_for_notification", AsyncMock(return_value=invoice)),
            patch.object(w, "_load_latest_waiting_invoice_for_master", AsyncMock(return_value=invoice)),
        ):
            result = await w._send_job(
                notifier=notifier,
                event=w.NotificationEvent.PRO_INVOICE_REMINDER,
                recipient=w.RecipientKind.MASTER,
                job=job,
                now_utc=datetime(2025, 1, 2, 12, 0, tzinfo=UTC),
                plan_cache={},
            )

        self.assertTrue(result)
        send_mock.assert_awaited()
