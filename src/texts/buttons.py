from __future__ import annotations

from src.texts.base import Translator, noop_t as _noop_t


def btn_confirm(*, t: Translator = _noop_t) -> str:
    return t("✅ Подтвердить")


def btn_restart(*, t: Translator = _noop_t) -> str:
    return t("🔁 Заново")


def btn_back(*, t: Translator = _noop_t) -> str:
    return t("◀️ Назад")


def btn_cancel(*, t: Translator = _noop_t) -> str:
    return t("❌ Отмена")


def btn_decline(*, t: Translator = _noop_t) -> str:
    return t("❌ Отклонить")


def btn_cancel_booking(*, t: Translator = _noop_t) -> str:
    return t("❌ Отменить запись")


def btn_go_pro(*, t: Translator = _noop_t) -> str:
    return t("🔓 Подключить Pro")


def btn_close(*, t: Translator = _noop_t) -> str:
    return t("✖️ Закрыть")
