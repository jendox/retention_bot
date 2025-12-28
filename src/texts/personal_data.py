from __future__ import annotations

from src.texts.base import Translator, noop_t as _noop_t


def btn_policy(*, t: Translator = _noop_t) -> str:
    return t("📄 Политика ПД")


def btn_agree(*, t: Translator = _noop_t) -> str:
    return t("✅ Согласен(на)")


def btn_disagree(*, t: Translator = _noop_t) -> str:
    return t("❌ Не согласен(на)")


def btn_understood(*, t: Translator = _noop_t) -> str:
    return t("✅ Понял(а)")


def consent_short(*, t: Translator = _noop_t) -> str:
    return t(
        "Перед регистрацией нужно согласие на обработку персональных данных.\n\n"
        "Мы храним имя, телефон, таймзону и историю записей — только чтобы бот работал: "
        "запись, уведомления и оплата Pro.\n"
        "Маркетинговых рассылок не делаем.",
    )


def policy_in_progress(*, t: Translator = _noop_t) -> str:
    return t("📄 Политика обработки персональных данных сейчас в работе. Скоро добавим файл.")


def consent_declined(*, t: Translator = _noop_t) -> str:
    return t("Без согласия на обработку персональных данных регистрация невозможна.")


def delete_client_warning(*, t: Translator = _noop_t) -> str:
    return t(
        "🗑 Удаление данных клиента\n\n"
        "Будут удалены профиль и все данные клиента в боте, включая историю записей.\n"
        "После удаления пользоваться ботом как клиент будет невозможно.\n\n"
        "Удалить данные?",
    )


def delete_master_warning(*, t: Translator = _noop_t) -> str:
    return t(
        "🗑 Удаление данных мастера\n\n"
        "Будут удалены профиль мастера, клиенты и записи, связанные с этим профилем.\n"
        "После удаления пользоваться ботом как мастер будет невозможно.\n\n"
        "Удалить данные?",
    )


def deleted_done(*, t: Translator = _noop_t) -> str:
    return t("✅ Данные удалены.")
