from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, UTC
from enum import StrEnum
from typing import Protocol

from src.notifications import NotificationEvent, RecipientKind


class DenyReason(StrEnum):
    NO_CHAT_ID = "no_chat_id"
    RECIPIENT_OFFLINE = "recipient_offline"
    CLIENT_NOTIFICATIONS_DISABLED = "client_notifications_disabled"
    MASTER_NOTIFICATIONS_DISABLED = "master_notifications_disabled"
    PRO_REQUIRED = "pro_required"
    EVENT_NOT_ALLOWED = "event_not_allowed"
    PAST_BOOKING = "past_booking"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: DenyReason | None = None
    detail: str | None = None

    @classmethod
    def allow(cls) -> "PolicyDecision":
        return cls(True)

    @classmethod
    def deny(cls, reason: DenyReason, *, detail: str | None = None) -> "PolicyDecision":
        return cls(False, reason=reason, detail=detail)


@dataclass(frozen=True)
class NotificationFacts:
    """
    Всё, что policy может использовать, должно быть передано извне (без DB).
    Для разных event часть полей может быть None.
    """
    event: NotificationEvent
    recipient: RecipientKind
    chat_id: int | None

    # План/тумблеры (передаются из handlers/use-cases)
    plan_is_pro: bool | None = None  # важно для client-facing событий
    master_notify_clients: bool | None = None
    client_notifications_enabled: bool | None = None

    # Доп. контекст
    booking_start_at_utc: datetime | None = None
    now_utc: datetime | None = None


class NotificationPolicy(Protocol):
    def check(self, facts: NotificationFacts) -> PolicyDecision: ...


class DefaultNotificationPolicy:
    """
    Политика:
    - master-facing уведомления: доступны всем (Free)
    - client-facing уведомления: Pro-only + оба тумблера
    - reminders: Pro-only + оба тумблера + только для будущих записей
    """

    MASTER_EVENTS_FREE: set[NotificationEvent] = {
        NotificationEvent.BOOKING_CREATED_PENDING,
        NotificationEvent.BOOKING_CANCELLED_BY_CLIENT,
        NotificationEvent.BOOKING_RESCHEDULED_BY_MASTER_NOTICE,
        NotificationEvent.WARNING_NEAR_CLIENTS_LIMIT,
        NotificationEvent.WARNING_NEAR_BOOKINGS_LIMIT,
        NotificationEvent.LIMIT_CLIENTS_REACHED,
        NotificationEvent.LIMIT_BOOKINGS_REACHED,
    }

    CLIENT_EVENTS_PRO: set[NotificationEvent] = {
        NotificationEvent.BOOKING_CONFIRMED,
        NotificationEvent.BOOKING_DECLINED,
        NotificationEvent.BOOKING_CREATED_CONFIRMED,
        NotificationEvent.BOOKING_CANCELLED_BY_MASTER,
        NotificationEvent.BOOKING_RESCHEDULED_BY_MASTER,
        NotificationEvent.REMINDER_24H,
        NotificationEvent.REMINDER_2H,
        NotificationEvent.FOLLOWUP_THANK_YOU,
    }

    def check(self, facts: NotificationFacts) -> PolicyDecision:
        if facts.chat_id is None:
            return PolicyDecision.deny(DenyReason.NO_CHAT_ID)

        # 1) Master-facing: разрешаем только известные события мастеру
        if facts.recipient == RecipientKind.MASTER:
            if facts.event in self.MASTER_EVENTS_FREE:
                return PolicyDecision.allow()
            return PolicyDecision.deny(DenyReason.EVENT_NOT_ALLOWED, detail=f"event={facts.event}")

        # 2) Client-facing: только определённые события
        if facts.recipient == RecipientKind.CLIENT:
            if facts.event not in self.CLIENT_EVENTS_PRO:
                return PolicyDecision.deny(DenyReason.EVENT_NOT_ALLOWED, detail=f"event={facts.event}")

            # Pro required
            if not facts.plan_is_pro:
                return PolicyDecision.deny(DenyReason.PRO_REQUIRED)

            # Тумблеры
            if not facts.master_notify_clients:
                return PolicyDecision.deny(DenyReason.MASTER_NOTIFICATIONS_DISABLED)
            if not facts.client_notifications_enabled:
                return PolicyDecision.deny(DenyReason.CLIENT_NOTIFICATIONS_DISABLED)

            # (опционально) защита от отправки в прошлое — полезно для reminders/кнопок cancel
            if facts.booking_start_at_utc is not None:
                now = facts.now_utc or datetime.now(UTC)
                if facts.booking_start_at_utc <= now:
                    return PolicyDecision.deny(DenyReason.PAST_BOOKING)

            return PolicyDecision.allow()

        return PolicyDecision.deny(DenyReason.UNKNOWN, detail=f"recipient={facts.recipient}")
