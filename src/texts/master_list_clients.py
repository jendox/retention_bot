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


def phone_sep(*, t: Translator = _noop_t) -> str:
    return t(" · ")


def btn_prev(*, t: Translator = _noop_t) -> str:
    return t("⬅️ Назад")


def btn_next(*, t: Translator = _noop_t) -> str:
    return t("Вперёд ➡️")


def btn_close(*, t: Translator = _noop_t) -> str:
    return t("✖️ Закрыть")


def offline_legend(*, t: Translator = _noop_t) -> str:
    return t("📵 — клиент без Telegram (офлайн).")


def no_clients_now(*, t: Translator = _noop_t) -> str:
    return t("Клиентов пока нет 👀")
