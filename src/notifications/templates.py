from __future__ import annotations

import logging

from src.notifications.context import BookingContext, LimitsContext, ReminderContext
from src.notifications.types import NotificationEvent, RecipientKind

logger = logging.getLogger("notification_template")


def render_limits_template(*, event: NotificationEvent, recipient: RecipientKind, context: LimitsContext) -> str:
    # Free
    if event == NotificationEvent.WARNING_NEAR_CLIENTS_LIMIT and recipient == RecipientKind.MASTER:
        return (
            "⚠️ Лимит клиентов на Free почти исчерпан.\n\n"
            f"<b>{context.usage.clients_count}</b> из <b>{context.clients_limit}</b> клиентов.\n"
            "В Pro лимитов нет."
        )
    # Free
    if event == NotificationEvent.WARNING_NEAR_BOOKINGS_LIMIT and recipient == RecipientKind.MASTER:
        return (
            "⚠️ Лимит записей на Free почти исчерпан.\n\n"
            f"<b>{context.usage.bookings_created_this_month}</b> из "
            f"<b>{context.bookings_limit}</b> новых записей в этом месяце.\n"
            "В Pro лимитов нет."
        )
    # Free
    if event == NotificationEvent.LIMIT_CLIENTS_REACHED and recipient == RecipientKind.MASTER:
        return (
            "🚫 Лимит клиентов на Free исчерпан.\n\n"
            f"<b>{context.usage.clients_count}</b> из <b>{context.clients_limit}</b>.\n"
            "Чтобы приглашать и добавлять больше клиентов — подключите Pro."
        )
    # Free
    if event == NotificationEvent.LIMIT_BOOKINGS_REACHED and recipient == RecipientKind.MASTER:
        return (
            "🚫 Лимит записей на Free исчерпан.\n\n"
            f"<b>{context.usage.bookings_created_this_month}</b> из <b>{context.bookings_limit}</b>.\n"
            "Чтобы создавать больше записей — подключите Pro."
        )

    logger.warning(
        "unsupported_template",
        extra={"event": event.value, "recipient": recipient.value},
    )
    return ""


def render_booking_template(*, event: NotificationEvent, recipient: RecipientKind, context: BookingContext) -> str:
    # Free
    if event == NotificationEvent.BOOKING_CREATED_PENDING and recipient == RecipientKind.MASTER:
        return (
            "📩 Новая запись на подтверждение\n\n"
            f"<b>Клиент:</b> {context.client_name}\n"
            f"<b>Дата и время:</b> {context.slot_str}\n"
            f"<b>Длительность:</b> {context.duration_min} мин.\n\n"
            "Подтвердить запись?"
        )
    # Pro
    if event == NotificationEvent.BOOKING_CONFIRMED and recipient == RecipientKind.CLIENT:
        return (
            "✅ Запись подтверждена мастером.\n\n"
            f"<b>Мастер:</b> {context.master_name}\n"
            f"<b>Дата и время:</b> {context.slot_str}\n"
            "Ждём вас 🙂"
        )
    # Pro
    if event == NotificationEvent.BOOKING_DECLINED and recipient == RecipientKind.CLIENT:
        return (
            "❌ Мастер отклонил запись.\n\n"
            f"<b>Мастер:</b> {context.master_name}\n"
            f"<b>Дата и время:</b> {context.slot_str}\n"
            "Пожалуйста, выберите другое время в разделе «➕ Записаться»."
        )
    # Pro
    if event == NotificationEvent.BOOKING_CREATED_CONFIRMED and recipient == RecipientKind.CLIENT:
        return (
            "✔️ Вам назначена запись\n\n"
            f"<b>Мастер:</b> {context.master_name}\n"
            f"<b>Дата и время:</b> {context.slot_str}\n"
            f"<b>Длительность:</b> {context.duration_min} мин.\n"
            "Если время не подходит — свяжитесь с мастером."
        )
    # Free
    if event == NotificationEvent.BOOKING_CANCELLED_BY_CLIENT and recipient == RecipientKind.MASTER:
        return (
            "❌ Запись отменена клиентом\n\n"
            f"<b>Клиент:</b> {context.client_name}\n"
            f"<b>Дата и время:</b> {context.slot_str}"
        )
    # Pro
    if event == NotificationEvent.BOOKING_CANCELLED_BY_MASTER and recipient == RecipientKind.CLIENT:
        return (
            "🚫 Запись отменена мастером.\n\n"
            f"<b>Мастер:</b> {context.master_name}\n"
            f"<b>Дата и время:</b> {context.slot_str}\n"
            "Если нужно — выберите другое время в разделе «➕ Записаться»."
        )
    # Pro
    if event == NotificationEvent.BOOKING_RESCHEDULED_BY_MASTER and recipient == RecipientKind.CLIENT:
        return (
            "🔄 Ваша запись перенесена.\n\n"
            f"<b>Мастер:</b> {context.master_name}\n"
            f"<b>Новая дата и время:</b> {context.slot_str}\n"
            f"<b>Длительность:</b> {context.duration_min} мин.\n"
            "Если время не подходит — свяжитесь с мастером."
        )
    # Free
    if event == NotificationEvent.BOOKING_RESCHEDULED_BY_MASTER_NOTICE and recipient == RecipientKind.MASTER:
        return (
            "✅ Запись перенесена.\n\n"
            f"<b>Клиент:</b> {context.client_name}\n"
            f"<b>Новая дата и время:</b> {context.slot_str}"
        )

    logger.warning(
        "unsupported_template",
        extra={"event": event.value, "recipient": recipient.value},
    )
    return ""


def render_reminder_template(*, event: NotificationEvent, recipient: RecipientKind, context: ReminderContext) -> str:
    # Pro
    if event == NotificationEvent.REMINDER_24H and recipient == RecipientKind.CLIENT:
        return (
            "Напоминание о записи ⏰\n\n"
            f"<b>Мастер:</b> {context.master_name}\n"
            f"<b>Дата и время:</b> {context.slot_str}\n"
            "До встречи 🙂"
        )
    # Pro
    if event == NotificationEvent.REMINDER_2H and recipient == RecipientKind.CLIENT:
        return (
            "Скоро запись ⏳\n\n"
            f"<b>Мастер:</b> {context.master_name}\n"
            f"<b>Дата и время:</b> {context.slot_str}\n"
            "Ждём вас 🙂"
        )
    # Pro
    if event == NotificationEvent.FOLLOWUP_THANK_YOU and recipient == RecipientKind.CLIENT:
        return (
            "Спасибо за визит 💛\n\n"
            "Если захотите записаться снова — откройте «➕ Записаться» в BeautyDesk."
        )

    logger.warning(
        "unsupported_template",
        extra={"event": event.value, "recipient": recipient.value},
    )
    return ""
