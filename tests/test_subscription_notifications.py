from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo


class NotificationTemplatesCoverageTests(unittest.TestCase):
    def test_all_notification_events_have_templates(self) -> None:
        from src.notifications import templates as t
        from src.notifications.types import NotificationEvent

        all_template_keys: set[tuple[object, object]] = set()
        for mapping in (
            t.BILLING_TEMPLATES,
            t.LIMITS_TEMPLATES,
            t.BOOKING_TEMPLATES,
            t.MASTER_TEMPLATES,
            t.REMINDER_TEMPLATES,
            t.ONBOARDING_TEMPLATES,
            t.SUBSCRIPTION_TEMPLATES,
        ):
            all_template_keys |= set(mapping.keys())

        events_with_templates = {event for event, _ in all_template_keys}
        missing = [event for event in NotificationEvent if event not in events_with_templates]
        self.assertEqual(missing, [])

    def test_all_templates_render_non_empty_text(self) -> None:
        from src.notifications import templates as t
        from src.notifications.context import (
            BillingContext,
            BookingContext,
            LimitsContext,
            OnboardingContext,
            ReminderContext,
            SubscriptionContext,
        )
        from src.use_cases.entitlements import Usage

        booking_ctx = BookingContext(
            booking_id=1,
            master_name="Master",
            client_name="Client",
            slot_str="01.01.2025 10:00",
            duration_min=60,
        )
        reminder_ctx = ReminderContext(master_name="Master", slot_str="01.01.2025 10:00")
        onboarding_ctx = OnboardingContext(master_name="Master")
        limits_ctx = LimitsContext(
            usage=Usage(clients_count=3, bookings_created_this_month=5),
            clients_limit=10,
            bookings_limit=20,
        )
        subscription_ctx = SubscriptionContext(master_name="Master", plan="pro", ends_on="01.01.2025", days_left=3)
        billing_ctx = BillingContext(master_name="Master")

        for fn in t.BILLING_TEMPLATES.values():
            self.assertTrue(str(fn(billing_ctx)).strip())
        for fn in t.LIMITS_TEMPLATES.values():
            self.assertTrue(str(fn(limits_ctx)).strip())
        for fn in t.BOOKING_TEMPLATES.values():
            self.assertTrue(str(fn(booking_ctx)).strip())
        for fn in t.MASTER_TEMPLATES.values():
            self.assertTrue(str(fn(booking_ctx)).strip())
        for fn in t.REMINDER_TEMPLATES.values():
            self.assertTrue(str(fn(reminder_ctx)).strip())
        for fn in t.ONBOARDING_TEMPLATES.values():
            self.assertTrue(str(fn(onboarding_ctx)).strip())
        for fn in t.SUBSCRIPTION_TEMPLATES.values():
            self.assertTrue(str(fn(subscription_ctx)).strip())


class SubscriptionSchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_trial_expiry_reminders_builds_expected_items(self) -> None:
        from src.datetime_utils import end_of_day_utc
        from src.repositories.scheduled_notification import ScheduledNotificationRepository
        from src.schemas.enums import Timezone

        session = AsyncMock()
        repo = ScheduledNotificationRepository(session)

        captured: dict[str, object] = {}

        async def _capture(**kwargs) -> int:
            captured.update(kwargs)
            return 1

        repo.upsert_subscription_reminders = AsyncMock(side_effect=_capture)  # type: ignore[method-assign]

        master_tz = Timezone.EUROPE_MINSK
        expiry_day = date(2025, 1, 14)
        trial_until_utc = end_of_day_utc(day=expiry_day, tz=master_tz)
        now_utc = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)

        await repo.schedule_trial_expiry_reminders(
            master_id=1,
            master_telegram_id=10,
            master_timezone=str(master_tz.value),
            trial_until_utc=trial_until_utc,
            now_utc=now_utc,
        )

        items = list(captured["items"])  # type: ignore[arg-type]
        self.assertEqual([e for e, _, __ in items], ["trial_expiring_d3", "trial_expiring_d1", "trial_expiring_d0"])
        self.assertEqual(captured["dedup_prefix"], "sub:trial")

        zone = ZoneInfo(str(master_tz.value))
        expected_days = [expiry_day - timedelta(days=3), expiry_day - timedelta(days=1), expiry_day]
        expected_due = [datetime.combine(day, time(11, 0), tzinfo=zone).astimezone(UTC) for day in expected_days]
        actual_due = [due for _, due, __ in items]
        self.assertEqual(actual_due, expected_due)

    async def test_schedule_pro_expiry_reminders_builds_expected_items(self) -> None:
        from src.datetime_utils import end_of_day_utc
        from src.repositories.scheduled_notification import ScheduledNotificationRepository
        from src.schemas.enums import Timezone

        session = AsyncMock()
        repo = ScheduledNotificationRepository(session)

        captured: dict[str, object] = {}

        async def _capture(**kwargs) -> int:
            captured.update(kwargs)
            return 1

        repo.upsert_subscription_reminders = AsyncMock(side_effect=_capture)  # type: ignore[method-assign]

        master_tz = Timezone.EUROPE_MINSK
        expiry_day = date(2025, 2, 10)
        paid_until_utc = end_of_day_utc(day=expiry_day, tz=master_tz)
        now_utc = datetime(2025, 2, 1, 12, 0, tzinfo=UTC)

        await repo.schedule_pro_expiry_reminders(
            master_id=1,
            master_telegram_id=10,
            master_timezone=str(master_tz.value),
            paid_until_utc=paid_until_utc,
            now_utc=now_utc,
        )

        items = list(captured["items"])  # type: ignore[arg-type]
        self.assertEqual(
            [e for e, _, __ in items],
            ["pro_expiring_d5", "pro_expiring_d2", "pro_expiring_d0", "pro_expired_recovery_d1"],
        )
        self.assertEqual(captured["dedup_prefix"], "sub:pro")

        zone = ZoneInfo(str(master_tz.value))
        expected_days = [
            expiry_day - timedelta(days=5),
            expiry_day - timedelta(days=2),
            expiry_day,
            expiry_day + timedelta(days=1),
        ]
        expected_due = [datetime.combine(day, time(11, 0), tzinfo=zone).astimezone(UTC) for day in expected_days]
        actual_due = [due for _, due, __ in items]
        self.assertEqual(actual_due, expected_due)

    async def test_schedule_pro_invoice_payment_reminder_uses_next_day_morning(self) -> None:
        from src.repositories.scheduled_notification import ScheduledNotificationRepository
        from src.schemas.enums import Timezone

        session = AsyncMock()
        repo = ScheduledNotificationRepository(session)

        captured: dict[str, object] = {}

        async def _capture(**kwargs) -> int:
            captured.update(kwargs)
            return 1

        repo.upsert_invoice_payment_reminder = AsyncMock(side_effect=_capture)  # type: ignore[method-assign]

        tz = Timezone.EUROPE_MINSK
        now_utc = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
        await repo.schedule_pro_invoice_payment_reminder(
            master_id=1,
            master_telegram_id=10,
            master_timezone=str(tz.value),
            invoice_id=99,
            now_utc=now_utc,
        )

        due_at = captured["due_at_utc"]
        self.assertIsInstance(due_at, datetime)
        self.assertEqual(due_at.astimezone(ZoneInfo(str(tz.value))).date(), date(2025, 1, 2))
        self.assertEqual(due_at.astimezone(ZoneInfo(str(tz.value))).hour, 11)
        self.assertEqual(captured["dedup_key"], "beautydesk:outbox:billing:pro_invoice_reminder:99")


class SubscriptionWorkerSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_trial_expiring_sends_upgrade_keyboard(self) -> None:
        from src.datetime_utils import end_of_day_utc, morning_utc
        from src.schemas.enums import Timezone
        from src.workers import reminders as w

        master = SimpleNamespace(id=1, name="M", timezone=Timezone.EUROPE_MINSK)
        expiry_day = date(2025, 1, 14)
        subscription = SimpleNamespace(
            trial_until=end_of_day_utc(day=expiry_day, tz=master.timezone),
            paid_until=None,
        )
        due_at = morning_utc(day=expiry_day - timedelta(days=3), tz=master.timezone)
        now_utc = datetime(2025, 1, 10, 12, 0, tzinfo=UTC)
        job = SimpleNamespace(master_id=1, due_at=due_at)

        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True))
        with (
            patch.object(w, "_load_master_for_notification", AsyncMock(return_value=master)),
            patch.object(w, "_load_subscription_for_master", AsyncMock(return_value=subscription)),
            patch.object(w, "_plan_is_pro", AsyncMock(return_value=False)),
        ):
            result = await w._send_subscription_job(
                notifier=notifier,
                event=w.NotificationEvent.TRIAL_EXPIRING_D3,
                job=job,
                chat_id=10,
                now_utc=now_utc,
                plan_cache={},
            )

        self.assertTrue(result)
        notifier.maybe_send.assert_awaited_once()
        request = notifier.maybe_send.await_args.args[0]
        self.assertEqual(request.context.ends_on, "14.01.2025")
        self.assertEqual(request.reply_markup.inline_keyboard[0][0].callback_data, "billing:pro:start")
        self.assertEqual(request.reply_markup.inline_keyboard[0][0].text, "💎 Подключить Pro")

    async def test_send_trial_expiring_skips_when_paid_active(self) -> None:
        from src.datetime_utils import end_of_day_utc, morning_utc
        from src.schemas.enums import Timezone
        from src.workers import reminders as w

        master = SimpleNamespace(id=1, name="M", timezone=Timezone.EUROPE_MINSK)
        expiry_day = date(2025, 1, 14)
        subscription = SimpleNamespace(
            trial_until=end_of_day_utc(day=expiry_day, tz=master.timezone),
            paid_until=end_of_day_utc(day=date(2025, 3, 1), tz=master.timezone),
        )
        due_at = morning_utc(day=expiry_day - timedelta(days=3), tz=master.timezone)
        now_utc = datetime(2025, 1, 10, 12, 0, tzinfo=UTC)
        job = SimpleNamespace(master_id=1, due_at=due_at)

        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True))
        with (
            patch.object(w, "_load_master_for_notification", AsyncMock(return_value=master)),
            patch.object(w, "_load_subscription_for_master", AsyncMock(return_value=subscription)),
            patch.object(w, "_plan_is_pro", AsyncMock(return_value=True)),
        ):
            result = await w._send_subscription_job(
                notifier=notifier,
                event=w.NotificationEvent.TRIAL_EXPIRING_D3,
                job=job,
                chat_id=10,
                now_utc=now_utc,
                plan_cache={},
            )

        self.assertFalse(result)
        notifier.maybe_send.assert_not_awaited()

    async def test_send_pro_expiring_sends_renew_keyboard(self) -> None:
        from src.datetime_utils import end_of_day_utc, morning_utc
        from src.schemas.enums import Timezone
        from src.workers import reminders as w

        master = SimpleNamespace(id=1, name="M", timezone=Timezone.EUROPE_MINSK)
        expiry_day = date(2025, 2, 10)
        subscription = SimpleNamespace(
            trial_until=None,
            paid_until=end_of_day_utc(day=expiry_day, tz=master.timezone),
        )
        due_at = morning_utc(day=expiry_day - timedelta(days=5), tz=master.timezone)
        now_utc = datetime(2025, 2, 5, 12, 0, tzinfo=UTC)
        job = SimpleNamespace(master_id=1, due_at=due_at)

        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True))
        with (
            patch.object(w, "_load_master_for_notification", AsyncMock(return_value=master)),
            patch.object(w, "_load_subscription_for_master", AsyncMock(return_value=subscription)),
            patch.object(w, "_plan_is_pro", AsyncMock(return_value=True)),
        ):
            result = await w._send_subscription_job(
                notifier=notifier,
                event=w.NotificationEvent.PRO_EXPIRING_D5,
                job=job,
                chat_id=10,
                now_utc=now_utc,
                plan_cache={},
            )

        self.assertTrue(result)
        notifier.maybe_send.assert_awaited_once()
        request = notifier.maybe_send.await_args.args[0]
        self.assertEqual(request.context.ends_on, "10.02.2025")
        self.assertEqual(request.reply_markup.inline_keyboard[0][0].callback_data, "billing:pro:start")
        self.assertEqual(request.reply_markup.inline_keyboard[0][0].text, "💎 Продлить Pro")

    async def test_send_pro_expired_recovery_sends_only_after_expiry(self) -> None:
        from src.datetime_utils import end_of_day_utc, morning_utc
        from src.schemas.enums import Timezone
        from src.workers import reminders as w

        master = SimpleNamespace(id=1, name="M", timezone=Timezone.EUROPE_MINSK)
        expiry_day = date(2025, 2, 10)
        subscription = SimpleNamespace(
            trial_until=None,
            paid_until=end_of_day_utc(day=expiry_day, tz=master.timezone),
        )
        due_at = morning_utc(day=expiry_day + timedelta(days=1), tz=master.timezone)
        now_utc = datetime(2025, 2, 11, 12, 0, tzinfo=UTC)
        job = SimpleNamespace(master_id=1, due_at=due_at)

        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True))
        with (
            patch.object(w, "_load_master_for_notification", AsyncMock(return_value=master)),
            patch.object(w, "_load_subscription_for_master", AsyncMock(return_value=subscription)),
            patch.object(w, "_plan_is_pro", AsyncMock(return_value=False)),
        ):
            result = await w._send_subscription_job(
                notifier=notifier,
                event=w.NotificationEvent.PRO_EXPIRED_RECOVERY_D1,
                job=job,
                chat_id=10,
                now_utc=now_utc,
                plan_cache={},
            )

        self.assertTrue(result)
        notifier.maybe_send.assert_awaited_once()
        request = notifier.maybe_send.await_args.args[0]
        self.assertEqual(request.reply_markup.inline_keyboard[0][0].callback_data, "billing:pro:start")


class BillingReminderWorkerSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_pro_invoice_reminder_sends_payment_button(self) -> None:
        from src.schemas.enums import Timezone
        from src.workers import reminders as w

        master = SimpleNamespace(id=1, name="M", timezone=Timezone.EUROPE_MINSK)
        invoice = SimpleNamespace(id=99, master_id=1, status="waiting", expires_at=datetime(2025, 1, 3, tzinfo=UTC))
        latest = SimpleNamespace(id=99)
        job = SimpleNamespace(
            master_id=1,
            invoice_id=99,
            chat_id=10,
            booking_id=None,
            booking_start_at=None,
            due_at=datetime(2025, 1, 2, tzinfo=UTC),
        )

        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True))
        with (
            patch.object(w, "_load_master_for_notification", AsyncMock(return_value=master)),
            patch.object(w, "_plan_is_pro", AsyncMock(return_value=False)),
            patch.object(w, "_load_invoice_for_notification", AsyncMock(return_value=invoice)),
            patch.object(w, "_load_latest_waiting_invoice_for_master", AsyncMock(return_value=latest)),
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
        notifier.maybe_send.assert_awaited_once()
        request = notifier.maybe_send.await_args.args[0]
        self.assertEqual(request.event, w.NotificationEvent.PRO_INVOICE_REMINDER)
        self.assertEqual(request.reply_markup.inline_keyboard[0][0].callback_data, "billing:pro:start")
        self.assertEqual(request.reply_markup.inline_keyboard[0][0].text, "💳 Перейти к оплате")

    async def test_send_pro_invoice_reminder_skips_when_invoice_not_latest(self) -> None:
        from src.schemas.enums import Timezone
        from src.workers import reminders as w

        master = SimpleNamespace(id=1, name="M", timezone=Timezone.EUROPE_MINSK)
        invoice = SimpleNamespace(id=99, master_id=1, status="waiting", expires_at=datetime(2025, 1, 3, tzinfo=UTC))
        latest = SimpleNamespace(id=100)
        job = SimpleNamespace(
            master_id=1,
            invoice_id=99,
            chat_id=10,
            booking_id=None,
            booking_start_at=None,
            due_at=datetime(2025, 1, 2, tzinfo=UTC),
        )

        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True))
        with (
            patch.object(w, "_load_master_for_notification", AsyncMock(return_value=master)),
            patch.object(w, "_plan_is_pro", AsyncMock(return_value=False)),
            patch.object(w, "_load_invoice_for_notification", AsyncMock(return_value=invoice)),
            patch.object(w, "_load_latest_waiting_invoice_for_master", AsyncMock(return_value=latest)),
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
        notifier.maybe_send.assert_not_awaited()
