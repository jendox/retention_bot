from __future__ import annotations

from enum import StrEnum


class Channel(StrEnum):
    TELEGRAM = "telegram"


class RecipientKind(StrEnum):
    MASTER = "master"
    CLIENT = "client"


class NotificationEvent(StrEnum):
    BOOKING_CREATED_PENDING = "booking_created_pending"
    BOOKING_CONFIRMED = "booking_confirmed"
    BOOKING_DECLINED = "booking_declined"
    BOOKING_CREATED_CONFIRMED = "booking_created_confirmed"
    BOOKING_CANCELLED_BY_CLIENT = "booking_cancelled_by_client"
    BOOKING_CANCELLED_BY_MASTER = "booking_cancelled_by_master"
    BOOKING_RESCHEDULED_BY_MASTER = "booking_rescheduled_by_master"
    BOOKING_RESCHEDULED_BY_MASTER_NOTICE = "booking_rescheduled_by_master_notice"

    WARNING_NEAR_CLIENTS_LIMIT = "warning_near_clients_limit"
    WARNING_NEAR_BOOKINGS_LIMIT = "warning_near_bookings_limit"
    LIMIT_CLIENTS_REACHED = "limit_clients_reached"
    LIMIT_BOOKINGS_REACHED = "limit_bookings_reached"

    REMINDER_24H = "reminder_24h"
    REMINDER_2H = "reminder_2h"
    FOLLOWUP_THANK_YOU = "followup_thank_you"

    MASTER_ATTENDANCE_NUDGE = "master_attendance_nudge"

    MASTER_ONBOARDING_ADD_FIRST_CLIENT = "master_onboarding_add_first_client"
    MASTER_ONBOARDING_ADD_FIRST_BOOKING = "master_onboarding_add_first_booking"

    TRIAL_EXPIRING_D3 = "trial_expiring_d3"
    TRIAL_EXPIRING_D1 = "trial_expiring_d1"
    TRIAL_EXPIRING_D0 = "trial_expiring_d0"

    PRO_EXPIRING_D5 = "pro_expiring_d5"
    PRO_EXPIRING_D2 = "pro_expiring_d2"
    PRO_EXPIRING_D0 = "pro_expiring_d0"
    PRO_EXPIRED_RECOVERY_D1 = "pro_expired_recovery_d1"

    PRO_INVOICE_REMINDER = "pro_invoice_reminder"
