from __future__ import annotations

from src.texts.base import Translator, noop_t as _noop_t


def invalid_command(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Некорректная команда.")


def not_your_booking(*, t: Translator = _noop_t) -> str:
    return t("❌ Это не твоя запись.")


def already_handled(*, t: Translator = _noop_t) -> str:
    return t("ℹ️ Эта запись уже обработана.")


def past_booking(*, t: Translator = _noop_t) -> str:
    return t("ℹ️ Нельзя подтвердить прошедшую запись.")


def failed(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Не удалось обработать запись.")


def done(*, t: Translator = _noop_t) -> str:
    return t("✅ Готово.")


def master_confirmed(*, client_name: str, slot_str: str, t: Translator = _noop_t) -> str:
    return t(
        f"✅ Запись подтверждена.\n\n<b>Клиент:</b> {client_name}\n<b>Дата/время:</b> {slot_str}\n",
    )


def client_confirmed(*, slot_str: str, t: Translator = _noop_t) -> str:
    return t(
        f"✅ Запись подтверждена мастером.\n\n<b>Дата/время:</b> {slot_str}\nЖдём тебя 🙂",
    )


def master_declined(*, client_name: str, slot_str: str, t: Translator = _noop_t) -> str:
    return t(
        f"❌ Запись отклонена.\n\n<b>Клиент:</b> {client_name}\n<b>Дата/время:</b> {slot_str}\n",
    )


def client_declined(*, slot_str: str, t: Translator = _noop_t) -> str:
    return t(
        "❌ Мастер отклонил запись.\n\n"
        f"<b>Дата/время:</b> {slot_str}\n"
        "Пожалуйста, выбери другое время в разделе «➕ Записаться».",
    )
