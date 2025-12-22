from __future__ import annotations

from src.texts.base import Translator, noop_t as _noop_t


def empty_list(*, t: Translator = _noop_t) -> str:
    return t(
        "Пока нет активных записей 🗓\n\n"
        "Чтобы записаться — нажми «➕ Записаться».",
    )


def title(*, t: Translator = _noop_t) -> str:
    return t("Твои активные записи 🗓")


def invalid_command(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Некорректная команда.")


def booking_not_found(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Запись не найдена или уже удалена.")


def forbidden(*, t: Translator = _noop_t) -> str:
    return t("❌ Это не ваша запись.")


def cannot_cancel(*, t: Translator = _noop_t) -> str:
    return t("❌ Не получилось отменить: запись уже обработана или время прошло.")


def cancelled_alert(*, t: Translator = _noop_t) -> str:
    return t("✅ Запись отменена.")


def cancelled_text(*, t: Translator = _noop_t) -> str:
    return t("❌ Запись отменена.")
