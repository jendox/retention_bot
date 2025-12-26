from __future__ import annotations

from src.texts.base import Translator, noop_t as _noop_t


def empty_short(*, t: Translator = _noop_t) -> str:
    return t("У тебя пока нет клиентов 👀\n\nКогда они появятся, ты увидишь их здесь.")


def empty_long(*, t: Translator = _noop_t) -> str:
    return t(
        "У тебя пока нет клиентов 👀\n\n"
        "Пригласи клиента по ссылке или добавь вручную, и они появятся здесь.",
    )


def title(*, page: int, total_pages: int, t: Translator = _noop_t) -> str:
    return t(f"👥 Клиенты (страница {page}/{total_pages})")


def choose_title(*, page: int, total_pages: int, t: Translator = _noop_t) -> str:
    return t(f"Выберите клиента (страница {page}/{total_pages}):")


def phone_sep(*, t: Translator = _noop_t) -> str:
    return t(" · ")


def btn_placeholder(*, t: Translator = _noop_t) -> str:
    return t("·")


def btn_find(*, t: Translator = _noop_t) -> str:
    return t("🔍 Найти")


def btn_select(*, t: Translator = _noop_t) -> str:
    return t("📌 Выбрать")


def btn_add(*, t: Translator = _noop_t) -> str:
    return t("➕ Добавить")


def btn_prev(*, t: Translator = _noop_t) -> str:
    return t("⬅️ Назад")


def btn_next(*, t: Translator = _noop_t) -> str:
    return t("Вперёд ➡️")


def btn_back_to_list(*, t: Translator = _noop_t) -> str:
    return t("◀️ Назад к списку")


def btn_back_to_choose(*, t: Translator = _noop_t) -> str:
    return t("◀️ Назад к выбору")


def btn_close(*, t: Translator = _noop_t) -> str:
    return t("✖️ Закрыть")


def offline_legend(*, t: Translator = _noop_t) -> str:
    return t("📵 — клиент без Telegram (офлайн).")


def no_clients_now(*, t: Translator = _noop_t) -> str:
    return t("Клиентов пока нет 👀")
