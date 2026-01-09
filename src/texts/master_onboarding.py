from __future__ import annotations

from aiogram import html

from src.texts.base import Translator, noop_t as _noop_t


def after_registration(*, name: str, t: Translator = _noop_t) -> str:
    safe_name = html.quote(name)
    return t(
        f"👋 {safe_name}, чтобы начать — добавь первого клиента.\n\n"
        "После этого можно создавать записи и вести историю.\n\n"
        "🎁 Pro‑триал начнётся автоматически после первой записи — чтобы оценить функции в деле.",
    )
