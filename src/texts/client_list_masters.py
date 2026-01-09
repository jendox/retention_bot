from __future__ import annotations

from src.texts.base import Translator, noop_t as _noop_t


def empty(*, t: Translator = _noop_t) -> str:
    return t(
        "Пока нет подключенных мастеров 👀\n\nПопроси мастера прислать тебе персональную ссылку.",
    )


def title(*, t: Translator = _noop_t) -> str:
    return t("Твои мастера 💇‍♀️\n")


def title_page(*, page: int, total_pages: int, t: Translator = _noop_t) -> str:
    return t(f"Твои мастера 💇‍♀️ (страница {page}/{total_pages})")


def choose_title(*, page: int, total_pages: int, t: Translator = _noop_t) -> str:
    return t(f"Выбери мастера (страница {page}/{total_pages}):")


def card_title(*, t: Translator = _noop_t) -> str:
    return t("Мастер")


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


def btn_book(*, t: Translator = _noop_t) -> str:
    return t("📅 Записаться")


def btn_edit_name(*, t: Translator = _noop_t) -> str:
    return t("✏️ Переименовать")


def ask_new_name(*, t: Translator = _noop_t) -> str:
    return t("Введи, как ты хочешь записать этого мастера у себя:")


def name_not_recognized(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Имя не должно быть пустым.")


def name_updated(*, t: Translator = _noop_t) -> str:
    return t("✅ Сохранено.")
