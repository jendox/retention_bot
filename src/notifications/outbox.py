from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from src.notifications.policy import NotificationFacts, NotificationPolicy
from src.notifications.types import NotificationEvent, RecipientKind
from src.repositories.scheduled_notification import ScheduledNotificationRepository


@dataclass(frozen=True)
class BookingClientOutboxNotification:
    event: NotificationEvent
    chat_id: int | None
    booking_id: int
    booking_start_at: datetime
    now_utc: datetime
    plan_is_pro: bool | None
    master_notify_clients: bool | None
    client_notifications_enabled: bool | None


async def maybe_enqueue_booking_client_notification(
    *,
    policy: NotificationPolicy,
    outbox: ScheduledNotificationRepository,
    request: BookingClientOutboxNotification,
) -> bool:
    if request.chat_id is None:
        return False

    decision = policy.check(
        NotificationFacts(
            event=request.event,
            recipient=RecipientKind.CLIENT,
            chat_id=int(request.chat_id),
            plan_is_pro=request.plan_is_pro,
            master_notify_clients=request.master_notify_clients,
            client_notifications_enabled=request.client_notifications_enabled,
            booking_start_at_utc=request.booking_start_at.astimezone(UTC),
            now_utc=request.now_utc,
        ),
    )
    if not decision.allowed:
        return False

    await outbox.enqueue_booking_client_notification(
        event=str(request.event.value),
        chat_id=int(request.chat_id),
        booking_id=int(request.booking_id),
        booking_start_at=request.booking_start_at,
        now_utc=request.now_utc,
    )
    return True
