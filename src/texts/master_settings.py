from __future__ import annotations

from dataclasses import dataclass

from src.plans import FREE_BOOKING_HORIZON_DAYS, PRO_BOOKING_HORIZON_DAYS
from src.texts.base import Translator, noop_t as _noop_t


def btn_phone(*, t: Translator = _noop_t) -> str:
    return t("📞 Телефон")


def btn_name(*, t: Translator = _noop_t) -> str:
    return t("👤 Имя")


def btn_timezone(*, t: Translator = _noop_t) -> str:
    return t("🌍 Таймзона")


def btn_work_days(*, t: Translator = _noop_t) -> str:
    return t("📆 Рабочие дни")


def btn_work_time(*, t: Translator = _noop_t) -> str:
    return t("🕒 Время работы")


def btn_slot_size(*, t: Translator = _noop_t) -> str:
    return t("⏱ Длительность записи ")


def btn_tariffs(*, t: Translator = _noop_t) -> str:
    return t("💎 Тарифы")


def btn_guide(*, t: Translator = _noop_t) -> str:
    return t("📘 Руководство")


def btn_edit_profile(*, t: Translator = _noop_t) -> str:
    return t("✏️ Редактировать профиль")


def btn_delete_data(*, t: Translator = _noop_t) -> str:
    return t("🗑 Удалить данные")


def btn_personal_data(*, t: Translator = _noop_t) -> str:
    return t("🛡 Персональные данные")


def btn_support(*, t: Translator = _noop_t) -> str:
    return t("💬 Поддержка")


def btn_notify(*, notify_clients: bool, plan_is_pro: bool, t: Translator = _noop_t) -> str:
    if not plan_is_pro:
        return t("🔒 Уведомлять клиентов: Pro")
    return (
        t("🔔 Уведомлять клиентов: включено ✅")
        if notify_clients
        else t("🔕 Уведомлять клиентов: выключено 🚫")
    )


def btn_notify_attendance(*, notify_attendance: bool, plan_is_pro: bool, t: Translator = _noop_t) -> str:
    if not plan_is_pro:
        return t("🔒 Напоминать отмечать явку: Pro")
    return (
        t("🔔 Напоминать отмечать явку: включено ✅")
        if notify_attendance
        else t("🔕 Напоминать отмечать явку: выключено 🚫")
    )


def title(*, t: Translator = _noop_t) -> str:
    return t("Настройки мастера ⚙️")


def plan_name(*, is_pro: bool, t: Translator = _noop_t) -> str:
    return t("Pro") if is_pro else t("Free")


@dataclass(frozen=True)
class ProFeaturesView:
    plan_is_pro: bool
    notify_clients: bool
    notify_attendance: bool


def pro_feature_status(*, enabled: bool, plan_is_pro: bool, t: Translator = _noop_t) -> str:
    if not plan_is_pro:
        return t("🔒 Pro")
    return t("включено ✅") if enabled else t("выключено 🚫")


def render_main(
    *,
    master_name: str,
    plan_label: str,
    tz_value: str,
    t: Translator = _noop_t,
) -> str:
    return t(
        f"{title(t=t)}\n\n"
        f"<b>Профиль:</b> {master_name}\n"
        f"<b>Тариф:</b> {plan_label}\n"
        f"<b>Таймзона:</b> {tz_value}",
    )


def render_details(
    *,
    phone: str,
    work_days: str,
    work_time: str,
    slot_size: str,
    pro: ProFeaturesView,
    t: Translator = _noop_t,
) -> str:
    return t(
        "\n\n"
        f"<b>Рабочие дни:</b> {work_days}\n"
        f"<b>Время работы:</b> {work_time}\n"
        f"<b>Длительность записи:</b> {slot_size}\n\n"
        f"<b>Телефон:</b> {phone}\n\n"
        "<b>💎 Pro‑функции</b>\n"
        f"• Горизонт записи: {int(FREE_BOOKING_HORIZON_DAYS)} дней (Free) / "
        f"{int(PRO_BOOKING_HORIZON_DAYS)} дней (Pro)\n"
        f"• Уведомлять клиентов: "
        f"{pro_feature_status(enabled=pro.notify_clients, plan_is_pro=pro.plan_is_pro, t=t)}\n"
        f"• Напоминать отмечать явку: "
        f"{pro_feature_status(enabled=pro.notify_attendance, plan_is_pro=pro.plan_is_pro, t=t)}",
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
    return t("Уведомлять клиентов можно в Pro.")


def notify_toggled(*, enabled: bool, t: Translator = _noop_t) -> str:
    return t("Уведомлять клиентов: включено ✅") if enabled else t("Уведомлять клиентов: выключено 🚫")


def notify_attendance_pro_only(*, t: Translator = _noop_t) -> str:
    return t("Напоминать отмечать явку можно в Pro.")


def ask_new_phone(*, t: Translator = _noop_t) -> str:
    return t("Введи новый телефон (в формате <code>375291234567</code>):")


def ask_new_name(*, t: Translator = _noop_t) -> str:
    return t("Введи новое имя (так тебя будут видеть клиенты):")


def invalid_name(*, t: Translator = _noop_t) -> str:
    return t("Не получилось разобрать имя. Попробуй ещё раз.")


def name_too_long(*, max_len: int, t: Translator = _noop_t) -> str:
    return t(f"Имя слишком длинное (макс. {int(max_len)} символов).")


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
        "Введи рабочее время в формате <code>H:MM-H:MM</code>.\n"
        "Можно использовать тире <code>-</code>, <code>–</code> или <code>—</code>.\n"
        "Также можно написать просто часы — тогда минуты будут <code>:00</code>.\n\n"
        "Примеры: <code>10:00-19:00</code>, <code>10:00–19:00</code>, <code>10-19</code>",
    )


def ask_slot_size(*, t: Translator = _noop_t) -> str:
    return t(
        "Введи длительность слота в минутах.\n"
        "Число должно быть кратно <code>5</code>.\n\n"
        "Например: <code>30</code>, <code>45</code>, <code>60</code>.",
    )


def invalid_phone(*, t: Translator = _noop_t) -> str:
    return t("Не смог разобрать номер. Введи в формате <code>375291234567</code>.")


def invalid_days(*, t: Translator = _noop_t) -> str:
    return t("Не смог разобрать дни. Пример: <code>1-5</code> или <code>1,3,5</code>.")


def invalid_work_time(*, t: Translator = _noop_t) -> str:
    return t(
        "Не получилось разобрать время. "
        "Примеры: <code>10:00-19:00</code>, <code>10:00–19:00</code>, <code>10-19</code>.",
    )


def invalid_slot_size(*, t: Translator = _noop_t) -> str:
    return t("Нужны минуты, кратные <code>5</code> (например: <code>30</code>, <code>45</code>, <code>60</code>).")


def minutes(*, value: int, t: Translator = _noop_t) -> str:
    return t(f"{value} мин")
