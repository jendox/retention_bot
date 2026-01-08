from __future__ import annotations

from src.texts.base import Translator, noop_t as _noop_t


def btn_timezone(*, t: Translator = _noop_t) -> str:
    return t("🌍 Таймзона")


def btn_name(*, t: Translator = _noop_t) -> str:
    return t("👤 Имя")


def btn_phone(*, t: Translator = _noop_t) -> str:
    return t("📞 Телефон")


def btn_edit_profile(*, t: Translator = _noop_t) -> str:
    return t("✏️ Редактировать профиль")


def btn_guide(*, t: Translator = _noop_t) -> str:
    return t("📘 Руководство")


def btn_notifications(*, enabled: bool, t: Translator = _noop_t) -> str:
    return t("🔔 Уведомления: включены ✅") if enabled else t("🔕 Уведомления: выключены 🚫")


def btn_delete_data(*, t: Translator = _noop_t) -> str:
    return t("🗑 Удалить данные")


def btn_personal_data(*, t: Translator = _noop_t) -> str:
    return t("🛡 Персональные данные")


def btn_support(*, t: Translator = _noop_t) -> str:
    return t("💬 Поддержка")


def render_settings(
    *,
    name: str,
    phone: str,
    tz_value: str,
    notifications_enabled: bool,
    t: Translator = _noop_t,
) -> str:
    notify_line = t("включены ✅") if notifications_enabled else t("выключены 🚫")
    return t(
        "Настройки клиента ⚙️\n\n"
        f"<b>Профиль:</b> {name}\n"
        f"<b>Телефон:</b> {phone}\n"
        f"<b>Таймзона:</b> {tz_value}\n"
        f"<b>Уведомления:</b> {notify_line}",
    )


def client_only(*, t: Translator = _noop_t) -> str:
    return t("Команда доступна клиентам после регистрации по ссылке мастера.")


def client_only_alert(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Команда доступна клиентам после регистрации.")


def choose_timezone(*, t: Translator = _noop_t) -> str:
    return t("Выбери таймзону:")


def invalid_timezone(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Некорректная таймзона.")


def timezone_updated(*, t: Translator = _noop_t) -> str:
    return t("✅ Таймзона обновлена.")


def saved(*, t: Translator = _noop_t) -> str:
    return t("✅ Сохранено.")


def ask_new_phone(*, t: Translator = _noop_t) -> str:
    return t("Введи новый телефон (в формате <code>375291234567</code>):")


def phone_not_recognized(*, t: Translator = _noop_t) -> str:
    return t("Не смог разобрать номер 🤔\n\nВведи номер в формате <code>375291234567</code>:")


def phone_updated(*, t: Translator = _noop_t) -> str:
    return t("✅ Телефон обновлён.")


def ask_new_name(*, t: Translator = _noop_t) -> str:
    return t("Введи новое имя:")


def invalid_name(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Имя не должно быть пустым.")


def name_too_long(*, max_len: int, t: Translator = _noop_t) -> str:
    return t(f"⚠️ Имя слишком длинное (максимум {max_len} символов).")


def guide_coming_soon(*, t: Translator = _noop_t) -> str:
    return t("📘 Руководство для клиентов скоро добавим.")
