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


def pro_config_missing() -> str:
    return "Оплата Pro пока не настроена. Напиши администратору через кнопку ниже."


def pro_paid() -> str:
    return "<b>Pro активирован ✅</b>"


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
        "• Уведомления/напоминания клиентам\n"
        "• Больше клиентов и записей\n"
        "• Перенос записи (reschedule)\n"
    )


def tariffs_message(
    *,
    plan_label: str,
    source: str,
    active_until: datetime | None,
    pro_days: int | None,
    pro_price_byn: float | None,
) -> str:
    until_line = ""
    if active_until is not None:
        until_line = f"\n<b>Активен до:</b> {active_until:%d.%m.%Y}"

    offer_line = ""
    if pro_days is not None and pro_price_byn is not None:
        offer_line = f"\n\n<b>Pro:</b> {int(pro_days)} дней за <b>{pro_price_byn:.2f} BYN</b>"

    return (
        "<b>Тарифы</b>\n\n"
        f"<b>Текущий:</b> {html.escape(plan_label)}"
        f"{until_line}\n"
        f"<b>Источник:</b> {html.escape(source)}"
        f"{plan_description()}"
        f"{offer_line}"
    )
