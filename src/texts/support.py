from __future__ import annotations

import html

from src.texts.base import Translator, noop_t as _noop_t


def btn_support(*, t: Translator = _noop_t) -> str:
    return t("💬 Поддержка")


def support_message(*, contact: str, t: Translator = _noop_t) -> str:
    safe_contact = html.escape(str(contact))
    return t(
        "💬 Поддержка\n\n"
        f"Напиши в поддержку: {safe_contact}\n\n"
        "Чтобы мы быстрее помогли, укажи:\n"
        "• кто ты (мастер/клиент)\n"
        "• имя и телефон\n"
        "• что именно не работает",
    )
