from __future__ import annotations

from enum import StrEnum


class Channel(StrEnum):
    TELEGRAM = "telegram"


class RecipientKind(StrEnum):
    MASTER = "master"
    CLIENT = "client"


class NotificationEvent(StrEnum):
    BOOKING_CREATED_PENDING = "booking_created_pending"
    BOOKING_CREATED_CONFIRMED = "booking_created_confirmed"
    BOOKING_CONFIRMED = "booking_confirmed"
    BOOKING_DECLINED = "booking_declined"
    BOOKING_CANCELLED_BY_CLIENT = "booking_cancelled_by_client"
    BOOKING_RESCHEDULED_BY_MASTER = "booking_rescheduled_by_master"

    WARNING_NEAR_CLIENTS_LIMIT = "warning_near_clients_limit"
