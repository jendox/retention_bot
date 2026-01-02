from __future__ import annotations

from src.texts.base import Translator, noop_t as _noop_t


def placeholder_empty(*, t: Translator = _noop_t) -> str:
    return t("—")


def label_default_client(*, t: Translator = _noop_t) -> str:
    return t("Клиент")


def label_offline_badge(*, t: Translator = _noop_t) -> str:
    return t(" 📵")


def cancelled(*, t: Translator = _noop_t) -> str:
    return t("❌ Отменено.")


def context_lost(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Контекст потерян. Начни заново.")


def generic_error(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Что-то пошло не так. Попробуй ещё раз.")


def too_many_requests(*, t: Translator = _noop_t) -> str:
    return t("⌛ Слишком часто. Попробуй через несколько секунд.")


def saved(*, t: Translator = _noop_t) -> str:
    return t("✅ Сохранено.")


def invalid_command(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Некорректная команда.")


def input_choose_action(*, t: Translator = _noop_t) -> str:
    return t("Выбери действие")


def subscription_payment_disclaimer(*, t: Translator = _noop_t) -> str:
    return t(
        "ℹ️ Мы <b>не принимаем оплату за твои услуги</b> и <b>не видим твоих доходов.</b> "
        "Оплата нужна <b>только</b> за подписку на бота.",
    )
