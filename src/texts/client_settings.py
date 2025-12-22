from __future__ import annotations

from src.texts.base import Translator, noop_t as _noop_t


def btn_timezone(*, t: Translator = _noop_t) -> str:
    return t("🌍 Таймзона")


def btn_notifications(*, enabled: bool, t: Translator = _noop_t) -> str:
    return t("🔔 Уведомления: включены ✅") if enabled else t("🔕 Уведомления: выключены 🚫")


def render_settings(*, name: str, tz_value: str, notifications_enabled: bool, t: Translator = _noop_t) -> str:
    notify_line = t("включены ✅") if notifications_enabled else t("выключены 🚫")
    return t(
        "Настройки клиента ⚙️\n\n"
        f"<b>Профиль:</b> {name}\n"
        f"<b>Таймзона:</b> {tz_value}\n"
        f"<b>Уведомления:</b> {notify_line}\n\n"
        "Что настроим?",
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
