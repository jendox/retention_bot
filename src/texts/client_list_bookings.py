from __future__ import annotations

from src.texts.base import Translator, noop_t as _noop_t


def empty_list(*, t: Translator = _noop_t) -> str:
    return t(
        "Пока нет активных записей 🗓\n\nЧтобы записаться — нажми «➕ Записаться».",
    )


def title(*, t: Translator = _noop_t) -> str:
    return t("Твои активные записи 🗓")


def title_page(*, page: int, total_pages: int, t: Translator = _noop_t) -> str:
    return t(f"Твои активные записи 🗓 (страница {page}/{total_pages})")


def choose_title(*, page: int, total_pages: int, t: Translator = _noop_t) -> str:
    return t(f"Выбери запись (страница {page}/{total_pages}):")


def details_title(*, t: Translator = _noop_t) -> str:
    return t("Детали записи 🗓")


def cancel_confirm(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Точно отменить запись?")


def btn_prev(*, t: Translator = _noop_t) -> str:
    return t("⬅️ Назад")


def btn_next(*, t: Translator = _noop_t) -> str:
    return t("Вперёд ➡️")


def btn_close(*, t: Translator = _noop_t) -> str:
    return t("✖️ Закрыть")


def btn_select_mode(*, t: Translator = _noop_t) -> str:
    return t("📌 Выбрать")


def btn_back_to_list(*, t: Translator = _noop_t) -> str:
    return t("◀️ Назад к списку")


def btn_more(*, t: Translator = _noop_t) -> str:
    return t("Показать ещё")


def btn_less(*, t: Translator = _noop_t) -> str:
    return t("Скрыть")


def btn_write_master(*, t: Translator = _noop_t) -> str:
    return t("💬 Написать мастеру")


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
