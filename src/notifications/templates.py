from __future__ import annotations

import logging
from collections.abc import Callable

from src.notifications.context import BookingContext, LimitsContext, ReminderContext
from src.notifications.types import NotificationEvent, RecipientKind

logger = logging.getLogger("notification_template")


def _limit_str(value: int | None) -> str:
    return "∞" if value is None else str(value)


LIMITS_TEMPLATES: dict[tuple[NotificationEvent, RecipientKind], Callable[[LimitsContext], str]] = {
    # Free
    (NotificationEvent.WARNING_NEAR_CLIENTS_LIMIT, RecipientKind.MASTER): lambda context: (
        "⚠️ Лимит клиентов на Free почти исчерпан.\n\n"
        f"<b>{context.usage.clients_count}</b> из <b>{_limit_str(context.clients_limit)}</b> клиентов.\n"
        "В Pro — без ограничений."
    ),
    (NotificationEvent.WARNING_NEAR_BOOKINGS_LIMIT, RecipientKind.MASTER): lambda context: (
        "⚠️ Лимит записей на Free почти исчерпан.\n\n"
        f"<b>{context.usage.bookings_created_this_month}</b> из "
        f"<b>{context.bookings_limit}</b> новых записей в этом месяце.\n"
        "В Pro — без ограничений."
    ),
    (NotificationEvent.LIMIT_CLIENTS_REACHED, RecipientKind.MASTER): lambda context: (
        "🚫 Лимит клиентов на Free исчерпан.\n\n"
        f"<b>{context.usage.clients_count}</b> из <b>{_limit_str(context.clients_limit)}</b>.\n"
        "Чтобы приглашать и добавлять больше клиентов — подключи Pro."
    ),
    (NotificationEvent.LIMIT_BOOKINGS_REACHED, RecipientKind.MASTER): lambda context: (
        "🚫 Лимит записей на Free исчерпан.\n\n"
        f"<b>{context.usage.bookings_created_this_month}</b> из <b>{_limit_str(context.bookings_limit)}</b>.\n"
        "Чтобы создавать больше записей — подключи Pro."
    ),
}

BOOKING_TEMPLATES: dict[tuple[NotificationEvent, RecipientKind], Callable[[BookingContext], str]] = {
    # Free
    (NotificationEvent.BOOKING_CREATED_PENDING, RecipientKind.MASTER): lambda context: (
        "📩 Новая запись на подтверждение.\n\n"
        f"<b>Клиент:</b> {context.client_name}\n"
        f"<b>Дата и время:</b> {context.slot_str}\n"
        f"<b>Длительность:</b> {context.duration_min} мин.\n\n"
        "Подтвердить запись?"
    ),
    (NotificationEvent.BOOKING_CANCELLED_BY_CLIENT, RecipientKind.MASTER): lambda context: (
        "❌ Клиент отменил запись.\n\n"
        f"<b>Клиент:</b> {context.client_name}\n"
        f"<b>Дата и время:</b> {context.slot_str}"
    ),
    (NotificationEvent.BOOKING_RESCHEDULED_BY_MASTER_NOTICE, RecipientKind.MASTER): lambda context: (
        "🔄 Запись перенесена.\n\n"
        f"<b>Клиент:</b> {context.client_name}\n"
        f"<b>Новая дата и время:</b> {context.slot_str}"
    ),
    # Pro
    (NotificationEvent.BOOKING_CONFIRMED, RecipientKind.CLIENT): lambda context: (
        "✅ Запись подтверждена.\n\n"
        f"<b>Мастер:</b> {context.master_name}\n"
        f"<b>Дата и время:</b> {context.slot_str}\n"
        "До встречи 🙂"
    ),
    (NotificationEvent.BOOKING_DECLINED, RecipientKind.CLIENT): lambda context: (
        "❌ Запись не подтверждена.\n\n"
        f"<b>Мастер:</b> {context.master_name}\n"
        f"<b>Дата и время:</b> {context.slot_str}\n"
        "Выбери другое время в разделе «➕ Записаться» 🙂"
    ),
    (NotificationEvent.BOOKING_CREATED_CONFIRMED, RecipientKind.CLIENT): lambda context: (
        "📌 Тебя записали.\n\n"
        f"<b>Мастер:</b> {context.master_name}\n"
        f"<b>Дата и время:</b> {context.slot_str}\n"
        f"<b>Длительность:</b> {context.duration_min} мин.\n"
        "Если время не подходит — напиши мастеру."
    ),
    (NotificationEvent.BOOKING_CANCELLED_BY_MASTER, RecipientKind.CLIENT): lambda context: (
        "❌ Запись отменена мастером.\n\n"
        f"<b>Мастер:</b> {context.master_name}\n"
        f"<b>Дата и время:</b> {context.slot_str}\n"
        "Если нужно — выбери другое время в разделе «➕ Записаться»."
    ),
    (NotificationEvent.BOOKING_RESCHEDULED_BY_MASTER, RecipientKind.CLIENT): lambda context: (
        "🔄 Запись перенесена.\n\n"
        f"<b>Мастер:</b> {context.master_name}\n"
        f"<b>Новая дата и время:</b> {context.slot_str}\n"
        f"<b>Длительность:</b> {context.duration_min} мин.\n"
        "Если время не подходит — напиши мастеру."
    ),
}

REMINDER_TEMPLATES: dict[tuple[NotificationEvent, RecipientKind], Callable[[ReminderContext], str]] = {
    # Pro
    (NotificationEvent.REMINDER_24H, RecipientKind.CLIENT): lambda context: (
        "⏰ Напоминание о записи.\n\n"
        f"<b>Мастер:</b> {context.master_name}\n"
        f"<b>Дата и время:</b> {context.slot_str}\n"
        "До встречи 🙂"
    ),
    (NotificationEvent.REMINDER_2H, RecipientKind.CLIENT): lambda context: (
        "⏳ Скоро запись.\n\n"
        f"<b>Мастер:</b> {context.master_name}\n"
        f"<b>Дата и время:</b> {context.slot_str}\n"
        "До встречи 🙂"
    ),
    (NotificationEvent.FOLLOWUP_THANK_YOU, RecipientKind.CLIENT): lambda context: (
        "💛 Спасибо за визит!\n\n"
        "Если захочешь записаться снова — открой «➕ Записаться» в BeautyDesk."
    ),
}


def render_limits_template(*, event: NotificationEvent, recipient: RecipientKind, context: LimitsContext) -> str:
    fn = LIMITS_TEMPLATES.get((event, recipient))
    if fn is None:
        logger.debug(
            "unsupported_template",
            extra={"event": event.value, "recipient": recipient.value},
        )
    return fn(context) if fn else ""


def render_booking_template(*, event: NotificationEvent, recipient: RecipientKind, context: BookingContext) -> str:
    fn = BOOKING_TEMPLATES.get((event, recipient))
    if fn is None:
        logger.debug(
            "unsupported_template",
            extra={"event": event.value, "recipient": recipient.value},
        )
    return fn(context) if fn else ""


def render_reminder_template(*, event: NotificationEvent, recipient: RecipientKind, context: ReminderContext) -> str:
    fn = REMINDER_TEMPLATES.get((event, recipient))
    if fn is None:
        logger.debug(
            "unsupported_template",
            extra={"event": event.value, "recipient": recipient.value},
        )
    return fn(context) if fn else ""
