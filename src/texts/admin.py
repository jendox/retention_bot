from __future__ import annotations

from src.texts.base import Translator, noop_t as _noop_t


def placeholder_empty(*, t: Translator = _noop_t) -> str:
    return t("—")


def status_label(*, is_pro: bool, source: str | None, t: Translator = _noop_t) -> str:
    if not is_pro:
        return t("Free")
    status = t("Pro")
    if source == "trial":
        status += t(" (trial)")
    elif source == "paid":
        status += t(" (оплачено)")
    return status


def render_plan_text(
    *,
    title: str,
    status: str,
    until: str,
    clients_current: int,
    clients_limit: str,
    bookings_current: int,
    bookings_limit: str,
    horizon_days: int,
    billing_contact: str,
    t: Translator = _noop_t,
) -> str:
    return t(
        f"{title}\n\n"
        f"<b>Тариф:</b> {status}\n"
        f"<b>Действует до:</b> {until}\n\n"
        f"<b>Клиенты:</b> {clients_current}/{clients_limit}\n"
        f"<b>Новые записи (мес):</b> {bookings_current}/{bookings_limit}\n"
        f"<b>Горизонт записи:</b> {horizon_days} дней\n\n"
        "Чтобы подключить Pro — напиши: "
        f"{billing_contact}",
    )


def usage_grant_pro(*, t: Translator = _noop_t) -> str:
    return t("Использование: /grant_pro <master_telegram_id> <days>")


def args_must_be_numbers_grant(*, t: Translator = _noop_t) -> str:
    return t("Аргументы должны быть числами: /grant_pro <master_telegram_id> <days>")


def days_must_be_positive(*, t: Translator = _noop_t) -> str:
    return t("days должен быть > 0")


def master_not_found(*, t: Translator = _noop_t) -> str:
    return t("Мастер не найден.")


def pro_activated(*, master_name: str, master_telegram_id: int, until: str, t: Translator = _noop_t) -> str:
    return t(
        "✅ Pro активирован\n\n"
        f"<b>Мастер:</b> {master_name} ({master_telegram_id})\n"
        f"<b>До:</b> {until}",
    )


def usage_revoke_pro(*, t: Translator = _noop_t) -> str:
    return t("Использование: /revoke_pro <master_telegram_id>")


def master_id_must_be_number(*, t: Translator = _noop_t) -> str:
    return t("master_telegram_id должен быть числом.")


def pro_revoked(*, changed: bool, t: Translator = _noop_t) -> str:
    return t("✅ Pro отключён.") if changed else t("ℹ️ Подписка не найдена (ничего не изменил).")


def usage_plan(*, t: Translator = _noop_t) -> str:
    return t("Использование: /plan <master_telegram_id>")


def title_master_plan(*, master_name: str, t: Translator = _noop_t) -> str:
    return t(f"Тариф мастера 💳\n<b>{master_name}</b>")


def title_my_plan(*, t: Translator = _noop_t) -> str:
    return t("Твой тариф 💳")


def master_only(*, t: Translator = _noop_t) -> str:
    return t("Команда доступна мастерам после регистрации.")
