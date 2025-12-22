from __future__ import annotations

from src.texts.base import Translator, noop_t as _noop_t


def btn_role_master(*, t: Translator = _noop_t) -> str:
    return t("💇 Я мастер")


def btn_role_client(*, t: Translator = _noop_t) -> str:
    return t("👤 Я клиент")


def choose_role(*, t: Translator = _noop_t) -> str:
    return t("Похоже, у тебя есть две роли 🙂\nВыбери, как зайти сейчас:")


def greet_unknown(*, link: str, t: Translator = _noop_t) -> str:
    return t(
        "Привет! 👋\n"
        "Я BeautyDesk — бот для записи к мастерам.\n\n"
        "Чтобы записаться, возьми ссылку у своего мастера.\n"
        "Если ты мастер и хочешь подключить бота — "
        f"<a href='{link}'>жми сюда</a>.",
    )


def role_not_recognized(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Не понял роль 😅 Выбери, пожалуйста, ещё раз:")
