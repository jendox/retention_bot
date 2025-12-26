from __future__ import annotations

from src.texts.base import Translator, noop_t as _noop_t


def clients_limit_reached(*, limit: int, t: Translator = _noop_t) -> str:
    return t(
        f"Лимит Free: {int(limit)} клиентов.\n"
        "С Pro вы сможете хранить всех клиентов без ограничений и не терять историю записей.",
    )


def bookings_limit_reached(*, limit: int, t: Translator = _noop_t) -> str:
    return t(
        f"Лимит Free: {int(limit)} записей в месяц.\n"
        "Pro снимает лимиты и позволяет вести запись без остановок.",
    )


def reschedule_pro_only(*, t: Translator = _noop_t) -> str:
    return t(
        "Перенос записи доступен в Pro.\n"
        "Это экономит время и снижает no-show: клиент получает новое подтверждение автоматически.",
    )


def no_show_value(*, t: Translator = _noop_t) -> str:
    return t(
        "Похоже, клиент не пришёл 😕\n"
        "Pro помогает снижать no-show: напоминания клиенту за 24ч и за 2ч.",
    )


def contact_message(*, contact: str, t: Translator = _noop_t) -> str:
    return t(
        "🔓 Подключить Pro\n\n"
        "Напиши сюда — поможем подключить подписку:\n"
        f"{contact}",
    )
