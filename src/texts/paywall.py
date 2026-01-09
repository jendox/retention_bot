from __future__ import annotations

import html

from src.texts.base import Translator, noop_t as _noop_t


def clients_limit_reached(*, limit: int, t: Translator = _noop_t) -> str:
    return t(
        f"Лимит Free: {int(limit)} клиентов.\n"
        "С Pro вы сможете хранить всех клиентов без ограничений и не терять историю записей.",
    )


def bookings_limit_reached(*, limit: int, t: Translator = _noop_t) -> str:
    return t(
        f"Лимит Free: {int(limit)} записей в месяц.\nPro снимает лимиты и позволяет вести запись без остановок.",
    )


def reschedule_pro_only(*, t: Translator = _noop_t) -> str:
    return t(
        "Перенос записи доступен в Pro.\n"
        "Это экономит время и снижает no-show: клиент получает новое подтверждение автоматически.",
    )


def no_show_value(*, t: Translator = _noop_t) -> str:
    return t(
        "Похоже, клиент не пришёл 😕\nPro помогает снижать no-show: напоминания клиенту за 24ч и за 2ч.",
    )


def contact_message(*, contact: str, t: Translator = _noop_t) -> str:
    safe_contact = html.escape(str(contact))
    return t(
        "💬 Поддержка\n\n"
        "Если возникли сложности с оплатой подписки Pro — напиши: "
        f"{safe_contact}\n\n"
        "Чтобы мы быстро нашли аккаунт, укажи своё имя мастера и телефон.",
    )
