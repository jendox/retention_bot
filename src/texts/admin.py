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
    text = (
        f"{title}\n\n"
        f"<b>Тариф:</b> {status}\n"
        f"<b>Действует до:</b> {until}\n\n"
        f"<b>Клиенты:</b> {clients_current}/{clients_limit}\n"
        f"<b>Новые записи (мес):</b> {bookings_current}/{bookings_limit}\n"
        f"<b>Горизонт записи:</b> {horizon_days} дней"
    )
    if not "Pro" in status:
        text += (
            "\n\n"
            "Чтобы подключить Pro — напиши: "
            f"{billing_contact}"
        )
    return t(text)


def usage_grant_pro(*, t: Translator = _noop_t) -> str:
    return t("Использование: /grant_pro <code>master_telegram_id</code> <code>days</code>")


def args_must_be_numbers_grant(*, t: Translator = _noop_t) -> str:
    return t("Аргументы должны быть числами: /grant_pro <code>master_telegram_id</code> <code>days</code>")


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
    return t("Использование: /revoke_pro <code>master_telegram_id</code>")


def master_id_must_be_number(*, t: Translator = _noop_t) -> str:
    return t("master_telegram_id должен быть числом.")


def pro_revoked(*, changed: bool, t: Translator = _noop_t) -> str:
    return t("✅ Pro отключён.") if changed else t("ℹ️ Подписка не найдена (ничего не изменил).")


def usage_plan(*, t: Translator = _noop_t) -> str:
    return t("Использование: /plan <code>master_telegram_id</code>")


def title_master_plan(*, master_name: str, t: Translator = _noop_t) -> str:
    return t(f"Тариф мастера 💳\n<b>{master_name}</b>")


def title_my_plan(*, t: Translator = _noop_t) -> str:
    return t("Твой тариф 💳")


def master_only(*, t: Translator = _noop_t) -> str:
    return t("Команда доступна мастерам после регистрации.")


def usage_invite_master(*, t: Translator = _noop_t) -> str:
    return t("Использование: /invite_master [ttl_hours]")


def invite_master_secret_missing(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Не задан `SECURITY__MASTER_INVITE_SECRET`, приглашения мастерам недоступны.")


def invite_master_bad_ttl(*, t: Translator = _noop_t) -> str:
    return t("⚠️ ttl_hours должен быть положительным числом.")


def invite_master_created(*, link: str, ttl_hours: int, t: Translator = _noop_t) -> str:
    return t(
        "✅ Ссылка для регистрации мастера готова.\n\n"
        f"{link}\n\n"
        f"Ссылка действует {ttl_hours} ч.",
    )
