from __future__ import annotations

from collections.abc import Callable

from src.notifications.context import (
    BillingContext,
    BookingContext,
    LimitsContext,
    OnboardingContext,
    ReminderContext,
    SubscriptionContext,
)
from src.notifications.types import NotificationEvent, RecipientKind
from src.observability.events import EventLogger

ev = EventLogger("notification_template")


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
        f"❌ Клиент отменил запись.\n\n<b>Клиент:</b> {context.client_name}\n<b>Дата и время:</b> {context.slot_str}"
    ),
    (NotificationEvent.BOOKING_RESCHEDULED_BY_MASTER_NOTICE, RecipientKind.MASTER): lambda context: (
        f"🔄 Запись перенесена.\n\n<b>Клиент:</b> {context.client_name}\n<b>Новая дата и время:</b> {context.slot_str}"
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
        "Если время не подходит — напиши мастеру или отмени запись."
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
        "Если новое время не подходит — напиши мастеру или отмени запись."
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
        "💛 Спасибо за визит!\n\nЕсли захочешь записаться снова — открой «➕ Записаться» в BeautyDesk."
    ),
}


MASTER_TEMPLATES: dict[tuple[NotificationEvent, RecipientKind], Callable[[BookingContext], str]] = {
    (NotificationEvent.MASTER_ATTENDANCE_NUDGE, RecipientKind.MASTER): lambda context: (
        "📌 Нужно отметить явку по записи.\n\n"
        f"<b>Клиент:</b> {context.client_name}\n"
        f"<b>Дата и время:</b> {context.slot_str}\n\n"
        "Выбери вариант ниже:"
    ),
}

ONBOARDING_TEMPLATES: dict[tuple[NotificationEvent, RecipientKind], Callable[[OnboardingContext], str]] = {
    (NotificationEvent.MASTER_ONBOARDING_ADD_FIRST_CLIENT, RecipientKind.MASTER): lambda context: (
        f"👋 {context.master_name}, чтобы начать — добавь первого клиента.\n\n"
        "После этого можно создавать записи и вести историю."
    ),
    (NotificationEvent.MASTER_ONBOARDING_ADD_FIRST_BOOKING, RecipientKind.MASTER): lambda context: (
        f"✨ {context.master_name}, клиент уже добавлен.\n\n"
        "Следующий шаг — создать первую запись (это займёт минуту).\n\n"
        "🎁 Pro‑триал начнётся автоматически после первой записи — чтобы ты оценил функции в деле."
    ),
}


SUBSCRIPTION_TEMPLATES: dict[tuple[NotificationEvent, RecipientKind], Callable[[SubscriptionContext], str]] = {
    (NotificationEvent.TRIAL_EXPIRING_D3, RecipientKind.MASTER): lambda context: (
        "⏳ До конца пробного периода Pro подписки осталось 3 дня.\n\n"
        f"<b>Доступ до:</b> {context.ends_on}\n\n"
        "Если хочешь сохранить напоминания и переносы — можно подключить Pro заранее."
    ),
    (NotificationEvent.TRIAL_EXPIRING_D1, RecipientKind.MASTER): lambda context: (
        "⏳ Пробный период Pro подписки заканчивается завтра.\n\n"
        f"<b>Доступ до:</b> {context.ends_on}\n\n"
        "Если Pro нужен дальше — можно подключить заранее."
    ),
    (NotificationEvent.TRIAL_EXPIRING_D0, RecipientKind.MASTER): lambda context: (
        "⏳ Сегодня последний день пробного периода Pro подписки.\n\n"
        f"<b>Доступ до конца дня:</b> {context.ends_on}\n\n"
        "Если хочешь продолжить пользоваться функциями Pro — можно подключить в любой момент."
    ),
    (NotificationEvent.PRO_EXPIRING_D5, RecipientKind.MASTER): lambda context: (
        "💎 Pro скоро закончится.\n\n"
        f"<b>Осталось:</b> {context.days_left} дн.\n"
        f"<b>Доступ до:</b> {context.ends_on}\n\n"
        "Чтобы продлить в спокойном режиме — можно оплатить заранее."
    ),
    (NotificationEvent.PRO_EXPIRING_D2, RecipientKind.MASTER): lambda context: (
        "💎 До окончания Pro осталось 2 дня.\n\n"
        f"<b>Доступ до:</b> {context.ends_on}\n\n"
        "Если хочешь без перерыва — лучше продлить заранее."
    ),
    (NotificationEvent.PRO_EXPIRING_D0, RecipientKind.MASTER): lambda context: (
        "💎 Сегодня заканчивается Pro.\n\n"
        f"<b>Доступ до конца дня:</b> {context.ends_on}\n\n"
        "Чтобы не потерять напоминания и переносы — можно продлить."
    ),
    (NotificationEvent.PRO_EXPIRED_RECOVERY_D1, RecipientKind.MASTER): lambda context: (
        "Pro истёк.\n\n"
        "Если хочешь вернуть напоминания и переносы — можно продлить Pro."
    ),
}


BILLING_TEMPLATES: dict[tuple[NotificationEvent, RecipientKind], Callable[[BillingContext], str]] = {
    (NotificationEvent.PRO_INVOICE_REMINDER, RecipientKind.MASTER): lambda context: (
        "💎 Похоже, ты начинал подключение Pro, но не успел оплатить.\n\n"
        "Если Pro всё ещё нужен — нажми кнопку ниже, мы снова покажем счёт.\n"
        "Если уже не актуально — просто игнорируй это сообщение."
    ),
}


def render_limits_template(*, event: NotificationEvent, recipient: RecipientKind, context: LimitsContext) -> str:
    fn = LIMITS_TEMPLATES.get((event, recipient))
    if fn is None:
        ev.debug("notifications.unsupported_template", template="limits", event=event.value, recipient=recipient.value)
    return fn(context) if fn else ""


def render_booking_template(*, event: NotificationEvent, recipient: RecipientKind, context: BookingContext) -> str:
    fn = BOOKING_TEMPLATES.get((event, recipient))
    if fn is None:
        fn = MASTER_TEMPLATES.get((event, recipient))
    if fn is None:
        ev.debug("notifications.unsupported_template", template="booking", event=event.value, recipient=recipient.value)
    return fn(context) if fn else ""


def render_reminder_template(*, event: NotificationEvent, recipient: RecipientKind, context: ReminderContext) -> str:
    fn = REMINDER_TEMPLATES.get((event, recipient))
    if fn is None:
        ev.debug(
            "notifications.unsupported_template",
            template="reminder",
            event=event.value,
            recipient=recipient.value,
        )
    return fn(context) if fn else ""


def render_onboarding_template(
    *,
    event: NotificationEvent,
    recipient: RecipientKind,
    context: OnboardingContext,
) -> str:
    fn = ONBOARDING_TEMPLATES.get((event, recipient))
    if fn is None:
        ev.debug(
            "notifications.unsupported_template",
            template="onboarding",
            event=event.value,
            recipient=recipient.value,
        )
    return fn(context) if fn else ""


def render_subscription_template(
    *,
    event: NotificationEvent,
    recipient: RecipientKind,
    context: SubscriptionContext,
) -> str:
    fn = SUBSCRIPTION_TEMPLATES.get((event, recipient))
    if fn is None:
        ev.debug(
            "notifications.unsupported_template",
            template="subscription",
            event=event.value,
            recipient=recipient.value,
        )
    return fn(context) if fn else ""


def render_billing_template(*, event: NotificationEvent, recipient: RecipientKind, context: BillingContext) -> str:
    fn = BILLING_TEMPLATES.get((event, recipient))
    if fn is None:
        ev.debug(
            "notifications.unsupported_template",
            template="billing",
            event=event.value,
            recipient=recipient.value,
        )
    return fn(context) if fn else ""
