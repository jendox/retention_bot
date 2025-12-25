from __future__ import annotations

from src.texts.base import Translator, noop_t as _noop_t
from src.use_cases.create_client_offline import CreateClientOfflineError

# ----- Buttons -----


def title_add_client(*, t: Translator = _noop_t) -> str:
    return t("Добавим клиента ✍️")


# ----- Errors / alerts -----

def err_for_preflight(error: CreateClientOfflineError | None, *, t: Translator = _noop_t) -> str:
    mapping: dict[CreateClientOfflineError | None, str] = {
        CreateClientOfflineError.MASTER_NOT_FOUND: t("⚠️ Профиль мастера не найден. Пройди регистрацию."),
        CreateClientOfflineError.INVALID_REQUEST: t("❌ Возникла ошибка. Попробуй ещё раз."),
        CreateClientOfflineError.PHONE_CONFLICT: t(
            "ℹ️ Клиент с таким телефоном уже есть в твоей базе.\n"
            "Проверь правильность введённого номера.",
        ),
        None: t("Неизвестная ошибка."),
    }
    return mapping.get(error, mapping[None])


def quota_reached(*, t: Translator = _noop_t) -> str:
    return t("Лимит клиентов исчерпан.")


def cancelled(*, t: Translator = _noop_t) -> str:
    return t("Добавление клиента отменено.")


def missing_data(*, t: Translator = _noop_t) -> str:
    return t("Не хватает данных, попробуй заново.")


def done_offline(*, t: Translator = _noop_t) -> str:
    return t("✅ Готово! Клиент добавлен (🔴 оффлайн)")


def ask_phone_conflict_retry(*, t: Translator = _noop_t) -> str:
    return t("Укажи номер телефона:")


# ----- Flow texts -----

def ask_name(*, t: Translator = _noop_t) -> str:
    return t("Добавим клиента ✍️\n\nКак зовут клиента?")


def name_not_recognized(*, t: Translator = _noop_t) -> str:
    return t("Имя не понял 😅 Введи, пожалуйста, имя клиента.")


def name_too_long(*, max_len: int, t: Translator = _noop_t) -> str:
    return t(f"Имя слишком длинное. Максимум {max_len} символов.")


def ask_phone(*, t: Translator = _noop_t) -> str:
    return t("Записал. Теперь номер телефона (для связи):")


def phone_not_recognized(*, t: Translator = _noop_t) -> str:
    return t("Нужен реальный номер в формате 375291234567, чтобы связаться с клиентом.")


def confirm(*, name: str, phone: str, t: Translator = _noop_t) -> str:
    return t(
        "Проверь, пожалуйста:\n\n"
        f"<b>Имя:</b> {name}\n"
        f"<b>Телефон:</b> {phone}\n"
        "Всё верно?",
    )
