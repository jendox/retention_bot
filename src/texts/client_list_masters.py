from __future__ import annotations

from src.texts.base import Translator, noop_t as _noop_t


def empty(*, t: Translator = _noop_t) -> str:
    return t(
        "Пока нет подключенных мастеров 👀\n\n"
        "Попроси мастера прислать тебе персональную ссылку.",
    )


def title(*, t: Translator = _noop_t) -> str:
    return t("Твои мастера 💇‍♀️\n")
