from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from src.notifications.types import NotificationEvent, RecipientKind
from src.use_cases.entitlements import Usage


class DenyReason(StrEnum):
    NO_CHAT_ID = "no_chat_id"
    CLIENT_NOTIFICATIONS_DISABLED = "client_notifications_disabled"
    MASTER_NOTIFICATIONS_DISABLED = "master_notifications_disabled"
    MASTER_ATTENDANCE_DISABLED = "master_attendance_disabled"
    MASTER_ONBOARDING_DISABLED = "master_onboarding_disabled"
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
    def allow(cls) -> PolicyDecision:
        return cls(True)

    @classmethod
    def deny(cls, reason: DenyReason, *, detail: str | None = None) -> PolicyDecision:
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
    master_notify_attendance: bool | None = None
    master_onboarding_nudges_enabled: bool | None = None
    client_notifications_enabled: bool | None = None

    # Доп. контекст
    booking_start_at_utc: datetime | None = None
    now_utc: datetime | None = None

    # Limits context (for master-facing warning/limit events)
    usage: Usage | None = None
    clients_limit: int | None = None
    bookings_limit: int | None = None


class NotificationPolicy(Protocol):
    def check(self, facts: NotificationFacts) -> PolicyDecision: ...


class DefaultNotificationPolicy:
    """
    Политика:
    - master-facing уведомления: доступны всем (Free)
    - client-facing уведомления: Pro-only + оба тумблера
    - reminders: Pro-only + оба тумблера + только для будущих записей
    """

    MASTER_ALLOWED_EVENTS: set[NotificationEvent] = {
        NotificationEvent.BOOKING_CREATED_PENDING,
        NotificationEvent.BOOKING_CANCELLED_BY_CLIENT,
        NotificationEvent.BOOKING_RESCHEDULED_BY_MASTER_NOTICE,
        NotificationEvent.WARNING_NEAR_CLIENTS_LIMIT,
        NotificationEvent.WARNING_NEAR_BOOKINGS_LIMIT,
        NotificationEvent.LIMIT_CLIENTS_REACHED,
        NotificationEvent.LIMIT_BOOKINGS_REACHED,
        NotificationEvent.MASTER_ATTENDANCE_NUDGE,
        NotificationEvent.MASTER_ONBOARDING_ADD_FIRST_CLIENT,
        NotificationEvent.MASTER_ONBOARDING_ADD_FIRST_BOOKING,
        NotificationEvent.TRIAL_EXPIRING_D3,
        NotificationEvent.TRIAL_EXPIRING_D1,
        NotificationEvent.TRIAL_EXPIRING_D0,
        NotificationEvent.PRO_EXPIRING_D5,
        NotificationEvent.PRO_EXPIRING_D2,
        NotificationEvent.PRO_EXPIRING_D0,
        NotificationEvent.PRO_EXPIRED_RECOVERY_D1,
        NotificationEvent.PRO_INVOICE_REMINDER,
    }

    MASTER_FREE_ONLY_EVENTS: set[NotificationEvent] = {
        NotificationEvent.WARNING_NEAR_CLIENTS_LIMIT,
        NotificationEvent.WARNING_NEAR_BOOKINGS_LIMIT,
        NotificationEvent.LIMIT_CLIENTS_REACHED,
        NotificationEvent.LIMIT_BOOKINGS_REACHED,
    }

    MASTER_EVENTS_PRO: set[NotificationEvent] = {
        NotificationEvent.MASTER_ATTENDANCE_NUDGE,
    }

    MASTER_EVENTS_ONBOARDING: set[NotificationEvent] = {
        NotificationEvent.MASTER_ONBOARDING_ADD_FIRST_CLIENT,
        NotificationEvent.MASTER_ONBOARDING_ADD_FIRST_BOOKING,
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

    _NEAR_LIMIT_THRESHOLD = 0.8

    def check(self, facts: NotificationFacts) -> PolicyDecision:
        if facts.chat_id is None:
            return PolicyDecision.deny(DenyReason.NO_CHAT_ID)

        if facts.recipient == RecipientKind.MASTER:
            return self._check_master(facts)

        if facts.recipient == RecipientKind.CLIENT:
            return self._check_client(facts)

        return PolicyDecision.deny(DenyReason.UNKNOWN, detail=f"recipient={facts.recipient.value}")

    def _check_master(self, facts: NotificationFacts) -> PolicyDecision:
        if facts.event not in self.MASTER_ALLOWED_EVENTS:
            return PolicyDecision.deny(DenyReason.EVENT_NOT_ALLOWED, detail=f"event={facts.event.value}")

        if facts.event in self.MASTER_EVENTS_ONBOARDING:
            return self._check_master_onboarding(facts)
        if facts.event in self.MASTER_EVENTS_PRO:
            return self._check_master_pro(facts)
        if facts.event in self.MASTER_FREE_ONLY_EVENTS:
            return self._check_master_free_only(facts)
        return PolicyDecision.allow()

    @staticmethod
    def _check_master_onboarding(facts: NotificationFacts) -> PolicyDecision:
        if facts.master_onboarding_nudges_enabled is False:
            return PolicyDecision.deny(DenyReason.MASTER_ONBOARDING_DISABLED)
        return PolicyDecision.allow()

    @staticmethod
    def _check_master_pro(facts: NotificationFacts) -> PolicyDecision:
        if not facts.plan_is_pro:
            return PolicyDecision.deny(DenyReason.PRO_REQUIRED)
        if facts.master_notify_attendance is False:
            return PolicyDecision.deny(DenyReason.MASTER_ATTENDANCE_DISABLED)
        return PolicyDecision.allow()

    def _check_master_free_only(self, facts: NotificationFacts) -> PolicyDecision:
        if facts.plan_is_pro is None:
            return PolicyDecision.deny(DenyReason.EVENT_NOT_ALLOWED, detail="free_only_event_requires_plan")
        if facts.plan_is_pro:
            return PolicyDecision.deny(DenyReason.EVENT_NOT_ALLOWED, detail="free_only_event_for_pro_master")
        if facts.usage is None:
            return PolicyDecision.deny(DenyReason.EVENT_NOT_ALLOWED, detail="limits_event_requires_usage")
        return self._check_master_limits_event(facts)

    def _check_master_limits_event(self, facts: NotificationFacts) -> PolicyDecision:
        if facts.event == NotificationEvent.WARNING_NEAR_CLIENTS_LIMIT:
            return self._check_warn_near_clients(facts)
        if facts.event == NotificationEvent.LIMIT_CLIENTS_REACHED:
            return self._check_clients_reached(facts)
        if facts.event == NotificationEvent.WARNING_NEAR_BOOKINGS_LIMIT:
            return self._check_warn_near_bookings(facts)
        if facts.event == NotificationEvent.LIMIT_BOOKINGS_REACHED:
            return self._check_bookings_reached(facts)
        return PolicyDecision.allow()

    def _check_warn_near_clients(self, facts: NotificationFacts) -> PolicyDecision:
        if facts.clients_limit is None:
            return PolicyDecision.deny(DenyReason.EVENT_NOT_ALLOWED, detail="clients_limit_missing")
        if facts.usage is None:
            return PolicyDecision.deny(DenyReason.EVENT_NOT_ALLOWED, detail="limits_event_requires_usage")
        if facts.usage.clients_count < int(facts.clients_limit * self._NEAR_LIMIT_THRESHOLD):
            return PolicyDecision.deny(DenyReason.EVENT_NOT_ALLOWED, detail="clients_threshold_not_reached")
        return PolicyDecision.allow()

    def _check_clients_reached(self, facts: NotificationFacts) -> PolicyDecision:
        if facts.clients_limit is None:
            return PolicyDecision.deny(DenyReason.EVENT_NOT_ALLOWED, detail="clients_limit_missing")
        if facts.usage is None:
            return PolicyDecision.deny(DenyReason.EVENT_NOT_ALLOWED, detail="limits_event_requires_usage")
        if facts.usage.clients_count < facts.clients_limit:
            return PolicyDecision.deny(DenyReason.EVENT_NOT_ALLOWED, detail="clients_limit_not_reached")
        return PolicyDecision.allow()

    def _check_warn_near_bookings(self, facts: NotificationFacts) -> PolicyDecision:
        if facts.bookings_limit is None:
            return PolicyDecision.deny(DenyReason.EVENT_NOT_ALLOWED, detail="bookings_limit_missing")
        if facts.usage is None:
            return PolicyDecision.deny(DenyReason.EVENT_NOT_ALLOWED, detail="limits_event_requires_usage")
        threshold = int(facts.bookings_limit * self._NEAR_LIMIT_THRESHOLD)
        if facts.usage.bookings_created_this_month < threshold:
            return PolicyDecision.deny(DenyReason.EVENT_NOT_ALLOWED, detail="bookings_threshold_not_reached")
        return PolicyDecision.allow()

    def _check_bookings_reached(self, facts: NotificationFacts) -> PolicyDecision:
        if facts.bookings_limit is None:
            return PolicyDecision.deny(DenyReason.EVENT_NOT_ALLOWED, detail="bookings_limit_missing")
        if facts.usage is None:
            return PolicyDecision.deny(DenyReason.EVENT_NOT_ALLOWED, detail="limits_event_requires_usage")
        if facts.usage.bookings_created_this_month < facts.bookings_limit:
            return PolicyDecision.deny(DenyReason.EVENT_NOT_ALLOWED, detail="bookings_limit_not_reached")
        return PolicyDecision.allow()

    def _check_client(self, facts: NotificationFacts) -> PolicyDecision:
        if facts.event not in self.CLIENT_EVENTS_PRO:
            return PolicyDecision.deny(DenyReason.EVENT_NOT_ALLOWED, detail=f"event={facts.event.value}")

        if not facts.plan_is_pro:
            return PolicyDecision.deny(DenyReason.PRO_REQUIRED)

        if not facts.master_notify_clients:
            return PolicyDecision.deny(DenyReason.MASTER_NOTIFICATIONS_DISABLED)
        if not facts.client_notifications_enabled:
            return PolicyDecision.deny(DenyReason.CLIENT_NOTIFICATIONS_DISABLED)

        if facts.booking_start_at_utc is not None and self._is_past_booking(facts):
            return PolicyDecision.deny(DenyReason.PAST_BOOKING)

        return PolicyDecision.allow()

    def _is_past_booking(self, facts: NotificationFacts) -> bool:
        now = facts.now_utc or datetime.now(UTC)
        assert facts.booking_start_at_utc is not None
        return facts.booking_start_at_utc <= now
