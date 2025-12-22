from __future__ import annotations

from src.texts.base import Translator, noop_t as _noop_t


def btn_phone(*, t: Translator = _noop_t) -> str:
    return t("📞 Телефон")


def btn_timezone(*, t: Translator = _noop_t) -> str:
    return t("🌍 Таймзона")


def btn_work_days(*, t: Translator = _noop_t) -> str:
    return t("📆 Рабочие дни")


def btn_work_time(*, t: Translator = _noop_t) -> str:
    return t("🕒 Время работы")


def btn_slot_size(*, t: Translator = _noop_t) -> str:
    return t("⏱ Длительность слота")


def btn_notify(*, is_pro: bool, t: Translator = _noop_t) -> str:
    return t("🔔 Уведомления клиенту") if is_pro else t("🔔 Уведомления клиенту (Pro)")


def title(*, t: Translator = _noop_t) -> str:
    return t("Настройки мастера ⚙️")


def plan_name(*, is_pro: bool, t: Translator = _noop_t) -> str:
    return t("Pro") if is_pro else t("Free")


def notify_line(*, notify_clients: bool, plan_is_pro: bool, t: Translator = _noop_t) -> str:
    line = t("включены ✅") if notify_clients else t("выключены 🚫")
    if not plan_is_pro:
        line += t(" (доступно в Pro)")
    return line


def footer_question(*, t: Translator = _noop_t) -> str:
    return t("Что настроим?")


def render_main(
    *,
    master_name: str,
    tz_value: str,
    notify_clients: bool,
    plan_is_pro: bool,
    t: Translator = _noop_t,
) -> str:
    return t(
        f"{title(t=t)}\n\n"
        f"<b>Профиль:</b> {master_name}\n"
        f"<b>Тариф:</b> {plan_name(is_pro=plan_is_pro, t=t)}\n"
        f"<b>Таймзона:</b> {tz_value}\n"
        f"<b>Уведомления клиенту:</b> {notify_line(notify_clients=notify_clients, plan_is_pro=plan_is_pro, t=t)}\n\n"
        f"{footer_question(t=t)}",
    )


def render_details(
    *,
    phone: str,
    work_days: str,
    work_time: str,
    slot_size: str,
    t: Translator = _noop_t,
) -> str:
    return t(
        "\n\n"
        f"<b>Телефон:</b> {phone}\n"
        f"<b>Рабочие дни:</b> {work_days}\n"
        f"<b>Время:</b> {work_time}\n"
        f"<b>Слот:</b> {slot_size}",
    )


def master_only(*, t: Translator = _noop_t) -> str:
    return t("Команда доступна мастерам после регистрации.")


def cancelled(*, t: Translator = _noop_t) -> str:
    return t("❌ Отменено.")


def choose_timezone(*, t: Translator = _noop_t) -> str:
    return t("Выбери таймзону:")


def invalid_timezone(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Некорректная таймзона.")


def timezone_updated(*, t: Translator = _noop_t) -> str:
    return t("✅ Таймзона обновлена.")


def notify_pro_only(*, t: Translator = _noop_t) -> str:
    return t("Уведомления клиенту доступны в Pro.")


def notify_toggled(*, enabled: bool, t: Translator = _noop_t) -> str:
    return t("Уведомления клиенту включены ✅") if enabled else t("Уведомления клиенту отключены 🚫")


def ask_new_phone(*, t: Translator = _noop_t) -> str:
    return t("Введи новый телефон (в формате <code>375291234567</code>):")


def ask_work_days(*, t: Translator = _noop_t) -> str:
    return t(
        "В какие дни недели ты работаешь?\n"
        "1 — Пн, 2 — Вт, 3 — Ср, 4 — Чт, 5 — Пт, 6 — Сб, 7 — Вс\n\n"
        "Примеры:\n"
        "• <code>1-5</code>\n"
        "• <code>1,3,5</code>",
    )


def ask_work_time(*, t: Translator = _noop_t) -> str:
    return t(
        "Введи рабочее время в формате <code>HH:MM-HH:MM</code>.\n"
        "Например: <code>10:00-19:00</code>",
    )


def ask_slot_size(*, t: Translator = _noop_t) -> str:
    return t(
        "Введи длительность слота в минутах.\n"
        "Например: <code>30</code>, <code>60</code>, <code>90</code>.",
    )


def invalid_phone(*, t: Translator = _noop_t) -> str:
    return t("Не смог разобрать номер. Введи в формате <code>375291234567</code>.")


def invalid_days(*, t: Translator = _noop_t) -> str:
    return t("Не смог разобрать дни. Пример: <code>1-5</code> или <code>1,3,5</code>.")


def invalid_work_time(*, t: Translator = _noop_t) -> str:
    return t("Не получилось разобрать время. Пример: <code>10:00-19:00</code>.")


def invalid_slot_size(*, t: Translator = _noop_t) -> str:
    return t("Нужны минуты из списка: 15, 20, 30, 45, 60, 90, 120.")


def minutes(*, value: int, t: Translator = _noop_t) -> str:
    return t(f"{value} мин")
