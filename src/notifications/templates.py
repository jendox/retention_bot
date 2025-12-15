from __future__ import annotations

from src.notifications.context import BookingContext, LimitsContext
from src.notifications.types import NotificationEvent, RecipientKind


def render_limits_template(*, event: NotificationEvent, recipient: RecipientKind, context: LimitsContext) -> str:
    if event == NotificationEvent.WARNING_NEAR_CLIENTS_LIMIT:
        return (
            "⚠️ Лимит клиентов на Free почти исчерпан.\n\n"
            f"<b>{context.usage.clients_count}</b> из <b>{context.clients_limit}</b> клиентов.\n"
            "В Pro лимитов нет."
        )

    raise ValueError(f"Unsupported template for event={event} recipient={recipient}")


def render_booking_template(*, event: NotificationEvent, recipient: RecipientKind, context: BookingContext) -> str:
    if event == NotificationEvent.BOOKING_CREATED_PENDING and recipient == RecipientKind.MASTER:
        return (
            "Новая запись на подтверждение 📩\n\n"
            f"<b>Клиент:</b> {context.client_name}\n"
            f"<b>Дата/время:</b> {context.slot_str}\n"
            f"<b>Длительность:</b> {context.duration_min} мин\n\n"
            "Подтвердить запись?"
        )

    if event == NotificationEvent.BOOKING_CREATED_CONFIRMED and recipient == RecipientKind.CLIENT:
        return (
            "Вам назначена запись ✔️\n\n"
            f"<b>Мастер:</b> {context.master_name}\n"
            f"<b>Дата/время:</b> {context.slot_str}\n"
            f"<b>Длительность:</b> {context.duration_min} мин\n"
            "Если время не подходит — свяжитесь с мастером."
        )

    if event == NotificationEvent.BOOKING_CONFIRMED and recipient == RecipientKind.CLIENT:
        return (
            "✅ Запись подтверждена мастером.\n\n"
            f"<b>Мастер:</b> {context.master_name}\n"
            f"<b>Дата/время:</b> {context.slot_str}\n"
            "Ждём вас 🙂"
        )

    if event == NotificationEvent.BOOKING_DECLINED and recipient == RecipientKind.CLIENT:
        return (
            "❌ Мастер отклонил запись.\n\n"
            f"<b>Мастер:</b> {context.master_name}\n"
            f"<b>Дата/время:</b> {context.slot_str}\n"
            "Пожалуйста, выбери другое время в разделе «➕ Записаться»."
        )

    if event == NotificationEvent.BOOKING_CANCELLED_BY_CLIENT and recipient == RecipientKind.MASTER:
        return (
            "❌ Запись отменена клиентом\n\n"
            f"<b>Клиент:</b> {context.client_name}\n"
            f"<b>Дата/время:</b> {context.slot_str}"
        )

    if event == NotificationEvent.BOOKING_RESCHEDULED_BY_MASTER and recipient == RecipientKind.CLIENT:
        return (
            "Ваша запись перенесена 🔄\n\n"
            f"<b>Мастер:</b> {context.master_name}\n"
            f"<b>Новая дата/время:</b> {context.slot_str}\n"
            f"<b>Длительность:</b> {context.duration_min} мин\n"
            "Если время не подходит — свяжитесь с мастером."
        )

    raise ValueError(f"Unsupported template for event={event} recipient={recipient}")
