from __future__ import annotations

import unittest

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.notifications.close import NOTIFICATION_CLOSE_CB
from src.notifications.context import (
    BillingContext,
    BookingContext,
    LimitsContext,
    OnboardingContext,
    ReminderContext,
    SubscriptionContext,
)
from src.notifications.renderer import render
from src.notifications.types import NotificationEvent, RecipientKind
from src.use_cases.entitlements import Usage


def _has_close(markup: InlineKeyboardMarkup | None) -> bool:
    if markup is None:
        return False
    for row in markup.inline_keyboard:
        for btn in row:
            if getattr(btn, "callback_data", None) == NOTIFICATION_CLOSE_CB:
                return True
    return False


class NotificationsCloseButtonTests(unittest.TestCase):
    def test_close_added_for_client_notifications(self) -> None:
        booking_ctx = BookingContext(
            booking_id=1,
            master_name="M",
            client_name="C",
            slot_str="01.01.2026 10:00",
            duration_min=60,
        )
        reminder_ctx = ReminderContext(master_name="M", slot_str="01.01.2026 10:00")

        for event in (
            NotificationEvent.BOOKING_CONFIRMED,
            NotificationEvent.BOOKING_DECLINED,
            NotificationEvent.BOOKING_CREATED_CONFIRMED,
            NotificationEvent.BOOKING_CANCELLED_BY_MASTER,
            NotificationEvent.BOOKING_RESCHEDULED_BY_MASTER,
        ):
            msg = render(event=event, recipient=RecipientKind.CLIENT, context=booking_ctx, reply_markup=None)
            self.assertTrue(_has_close(msg.reply_markup), msg=f"missing close for {event.value}/client")

        for event in (
            NotificationEvent.REMINDER_24H,
            NotificationEvent.REMINDER_2H,
            NotificationEvent.FOLLOWUP_THANK_YOU,
        ):
            msg = render(event=event, recipient=RecipientKind.CLIENT, context=reminder_ctx, reply_markup=None)
            self.assertTrue(_has_close(msg.reply_markup), msg=f"missing close for {event.value}/client")

    def test_close_added_for_master_info_notifications(self) -> None:
        booking_ctx = BookingContext(
            booking_id=1,
            master_name="M",
            client_name="C",
            slot_str="01.01.2026 10:00",
            duration_min=60,
        )
        usage = Usage(clients_count=9, bookings_created_this_month=19)
        limits_ctx = LimitsContext(usage=usage, clients_limit=10, bookings_limit=20)
        onboarding_ctx = OnboardingContext(master_name="M")
        billing_ctx = BillingContext(master_name="M")
        subscription_ctx = SubscriptionContext(master_name="M", plan="pro", ends_on="01.01.2026", days_left=3)

        for event in (
            NotificationEvent.BOOKING_CANCELLED_BY_CLIENT,
            NotificationEvent.BOOKING_RESCHEDULED_BY_MASTER_NOTICE,
        ):
            msg = render(event=event, recipient=RecipientKind.MASTER, context=booking_ctx, reply_markup=None)
            self.assertTrue(_has_close(msg.reply_markup), msg=f"missing close for {event.value}/master")

        for event in (
            NotificationEvent.WARNING_NEAR_CLIENTS_LIMIT,
            NotificationEvent.WARNING_NEAR_BOOKINGS_LIMIT,
            NotificationEvent.LIMIT_CLIENTS_REACHED,
            NotificationEvent.LIMIT_BOOKINGS_REACHED,
        ):
            msg = render(event=event, recipient=RecipientKind.MASTER, context=limits_ctx, reply_markup=None)
            self.assertTrue(_has_close(msg.reply_markup), msg=f"missing close for {event.value}/master")

        for event in (
            NotificationEvent.MASTER_ONBOARDING_ADD_FIRST_CLIENT,
            NotificationEvent.MASTER_ONBOARDING_ADD_FIRST_BOOKING,
        ):
            msg = render(event=event, recipient=RecipientKind.MASTER, context=onboarding_ctx, reply_markup=None)
            self.assertTrue(_has_close(msg.reply_markup), msg=f"missing close for {event.value}/master")

        for event in (
            NotificationEvent.TRIAL_EXPIRING_D3,
            NotificationEvent.TRIAL_EXPIRING_D1,
            NotificationEvent.TRIAL_EXPIRING_D0,
            NotificationEvent.PRO_EXPIRING_D5,
            NotificationEvent.PRO_EXPIRING_D2,
            NotificationEvent.PRO_EXPIRING_D0,
            NotificationEvent.PRO_EXPIRED_RECOVERY_D1,
        ):
            msg = render(event=event, recipient=RecipientKind.MASTER, context=subscription_ctx, reply_markup=None)
            self.assertTrue(_has_close(msg.reply_markup), msg=f"missing close for {event.value}/master")

        msg = render(
            event=NotificationEvent.PRO_INVOICE_REMINDER,
            recipient=RecipientKind.MASTER,
            context=billing_ctx,
            reply_markup=None,
        )
        self.assertTrue(_has_close(msg.reply_markup))

    def test_close_not_added_for_action_required_master_notifications(self) -> None:
        booking_ctx = BookingContext(
            booking_id=1,
            master_name="M",
            client_name="C",
            slot_str="01.01.2026 10:00",
            duration_min=60,
        )
        msg = render(
            event=NotificationEvent.BOOKING_CREATED_PENDING,
            recipient=RecipientKind.MASTER,
            context=booking_ctx,
            reply_markup=None,
        )
        self.assertFalse(_has_close(msg.reply_markup))

        msg = render(
            event=NotificationEvent.MASTER_ATTENDANCE_NUDGE,
            recipient=RecipientKind.MASTER,
            context=booking_ctx,
            reply_markup=None,
        )
        self.assertFalse(_has_close(msg.reply_markup))

    def test_close_appended_without_losing_existing_buttons(self) -> None:
        booking_ctx = BookingContext(
            booking_id=1,
            master_name="M",
            client_name="C",
            slot_str="01.01.2026 10:00",
            duration_min=60,
        )
        original = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="X", callback_data="x:1")]],
        )
        msg = render(
            event=NotificationEvent.BOOKING_CREATED_CONFIRMED,
            recipient=RecipientKind.CLIENT,
            context=booking_ctx,
            reply_markup=original,
        )
        self.assertEqual(msg.reply_markup.inline_keyboard[0][0].callback_data, "x:1")
        self.assertTrue(_has_close(msg.reply_markup))
