from __future__ import annotations

import html
from datetime import datetime


def pro_invoice_created(*, days: int, price_byn: float) -> str:
    return (
        "<b>Оплата Pro</b>\n\n"
        f"Доступ: <b>{int(days)}</b> дней\n"
        f"Сумма: <b>{price_byn:.2f} BYN</b>\n\n"
        "Нажми «Оплатить» и затем «Проверить оплату»."
    )


def pro_create_invoice_confirm() -> str:
    return "<b>Создать счёт на оплату подписки Pro?</b>"


def pro_waiting_invoice_notice() -> str:
    return "<b>Есть выставленный счёт</b>\nМожно оплатить или проверить оплату."


def pro_config_missing() -> str:
    return "Оплата Pro пока не настроена. Напиши администратору через кнопку ниже."


def pro_paid() -> str:
    return "<b>Pro активирован ✅</b>"


def pro_paid_until(*, paid_until: datetime | None) -> str:
    if paid_until is None:
        return "<b>Оплата найдена ✅</b>\n\nPro активирован."
    return f"<b>Оплата найдена ✅</b>\n\nPro активирован до <b>{paid_until:%d.%m.%Y}</b>."


def pro_paid_alert(*, paid_until: datetime | None) -> str:
    if paid_until is None:
        return "Оплата прошла успешно ✅\nТариф Pro активирован."
    return f"Оплата прошла успешно ✅\nТариф Pro активирован до {paid_until:%d.%m.%Y}."


def pro_paid_message(*, paid_until: datetime | None) -> str:
    until_line = ""
    if paid_until is not None:
        until_line = f"Тариф <b>Pro</b> активирован до <b>{paid_until:%d.%m.%Y}</b>.\n"

    return (
        "<b>Оплата прошла успешно ✅</b>\n"
        f"{until_line}"
        "\n"
        "Теперь бот будет:\n"
        "• автоматически напоминать клиентам о записи;\n"
        "• помогать переносить запись без переписок;\n"
        "• хранить всю базу клиентов в одном месте.\n"
        "\n"
        "Спасибо за оплату! Если будут вопросы или идеи, что улучшить — просто напиши сюда в чат."
    )


def pro_already_active() -> str:
    return "Pro уже активен. Для продления открой «Тарифы»."


def pro_still_waiting() -> str:
    return "Платёж ещё не найден. Если ты только что оплатил — попробуй ещё раз через 10–30 секунд."


def pro_expired() -> str:
    return "Счёт просрочен. Создай новый счёт для оплаты."


def pro_canceled() -> str:
    return "Счёт отменён. Создай новый счёт для оплаты."


def pro_error() -> str:
    return "Не удалось проверить оплату. Попробуй ещё раз позже."


def btn_pay() -> str:
    return "💳 Оплатить"


def btn_check() -> str:
    return "🔄 Проверить оплату"


def btn_close() -> str:
    return "Закрыть"


def btn_contact() -> str:
    return "💬 Написать"


def btn_new_invoice() -> str:
    return "🧾 Новый счёт"


def contact_message(*, contact: str) -> str:
    return f"Для подключения Pro напиши: {html.escape(contact)}"


def plan_title(*, plan: str) -> str:
    return f"<b>Тариф:</b> {html.escape(plan)}"


def plan_description() -> str:
    return (
        "\n\n<b>Pro открывает:</b>\n"
        "• Автонапоминания клиентам о записи\n"
        "• Больше клиентов и записей\n"
        "• Перенос записи (reschedule)\n"
    )


def _format_price_byn(price_byn: float) -> str:
    if float(price_byn).is_integer():
        return str(int(price_byn))
    return f"{price_byn:.2f}"


def _format_tariffs_message(
    current_plan_line: str,
    price_str: str,
    pro_days: int,
) -> str:
    return (
        "<b>Тарифы</b>\n\n"
        f"{current_plan_line}\n\n"
        "Что даёт <b>Pro</b>:\n"
        "• Автонапоминания клиентам о записи (меньше no‑show и забытых визитов)\n"
        "• Неограниченная база клиентов и записей\n"
        "• Быстрый перенос записи без лишней переписки\n"
        "• Напоминания отметить явку клиента\n\n"
        f"Стоимость <b>Pro</b>: <b>{price_str} BYN</b> за <b>{pro_days}</b> дней."
    )


def tariffs_message(
    *,
    plan_label: str,
    source: str,
    active_until: datetime | None,
    pro_days: int | None,
    pro_price_byn: float | None,
) -> str:
    until_str = active_until.strftime("%d.%m.%Y") if active_until is not None else None
    price_str = _format_price_byn(float(pro_price_byn)) if pro_price_byn is not None else None

    if source == "trial" and until_str and pro_days and price_str:
        current_plan_line = (
            f"Сейчас у тебя активен пробный <b>Pro</b> до <b>{until_str}</b>.\n"
            "После этого можно продолжить на платной основе."
        )
        return _format_tariffs_message(current_plan_line, price_str, int(pro_days))

    if plan_label.lower().startswith("pro") and until_str and pro_days and price_str:
        current_plan_line = f"Сейчас у тебя активен тариф <b>Pro</b> до <b>{until_str}</b>."
        return _format_tariffs_message(current_plan_line, price_str, int(pro_days))

    if pro_days and price_str:
        current_plan_line = (
            "Сейчас у тебя активен бесплатный тариф <b>Free</b>.\n\n"
            "Что даёт <b>Free</b>:\n"
            "• Ведение клиентов и записей в одном месте\n"
            "• Просмотр расписания в Telegram"
        )
        return _format_tariffs_message(current_plan_line, price_str, int(pro_days))

    return (
        "<b>Тарифы</b>\n\n"
        f"<b>Текущий:</b> {html.escape(plan_label)}\n"
        f"<b>Источник:</b> {html.escape(source)}"
    )


def tariffs_primary_button(*, source: str, pro_days: int, pro_price_byn: float) -> str:
    price = _format_price_byn(float(pro_price_byn))
    if source == "trial":
        return f"Остаться на Pro за {price} BYN"
    if source in {"paid", "pro"}:
        return f"Продлить Pro за {price} BYN"
    return f"Перейти на Pro за {price} BYN"


def tariffs_secondary_button(*, source: str) -> str:
    if source == "free":
        return "Остаться на Free"
    return "Решу позже"
