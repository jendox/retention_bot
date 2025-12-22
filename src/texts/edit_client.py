from __future__ import annotations

from src.texts.base import Translator, noop_t as _noop_t

# ----- Buttons -----


def btn_edit_name(*, t: Translator = _noop_t) -> str:
    return t("✏️ Изменить имя")


def btn_edit_phone(*, t: Translator = _noop_t) -> str:
    return t("📞 Изменить телефон")


def btn_back_to_search(*, t: Translator = _noop_t) -> str:
    return t("◀️ Назад к поиску")


# ----- Flow texts -----

def client_card(*, name: str | None, phone: str | None, is_offline: bool, t: Translator = _noop_t) -> str:
    from src.texts.common import placeholder_empty
    name_ = name or placeholder_empty(t=t)
    phone_ = phone or placeholder_empty(t=t)
    offline = t("да") if is_offline else t("нет")
    return t(
        "Карточка клиента 👤\n\n"
        f"<b>Имя:</b> {name_}\n"
        f"<b>Телефон:</b> {phone_}\n"
        f"<b>Оффлайн:</b> {offline}",
    )


def ask_name_or_phone(*, t: Translator = _noop_t) -> str:
    return t("Введи имя или телефон клиента для поиска:")


def name_or_phone_required(*, t: Translator = _noop_t) -> str:
    return t("Нужно ввести имя или телефон клиента.")


def master_profile_not_found(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Профиль мастера не найден.")


def no_clients_found(*, t: Translator = _noop_t) -> str:
    return t("ℹ️ Не нашёл клиентов по запросу. Попробуй другое имя или телефон.")


def choose_client(*, t: Translator = _noop_t) -> str:
    return t("Нашёл клиентов. Выбери нужного:")


def invalid_client(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Некорректный клиент.")


def client_not_found_in_results(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Не нашёл клиента в списке, попробуй заново.")


def ask_new_name(*, t: Translator = _noop_t) -> str:
    return t("Введи новое имя клиента:")


def name_not_recognized(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Имя не понял 😅 Введи, пожалуйста, имя клиента.")


def name_updated(*, t: Translator = _noop_t) -> str:
    return t("✅ Имя обновлено.")


def ask_new_phone(*, t: Translator = _noop_t) -> str:
    return t("Введи новый телефон клиента (в формате <code>375291234567</code>):")


def phone_not_recognized(*, t: Translator = _noop_t) -> str:
    return t("Не смог разобрать номер 🤔\n\nВведи номер в формате <code>375291234567</code>:")


def phone_conflict(*, t: Translator = _noop_t) -> str:
    return t("У тебя уже есть клиент с таким телефоном. Телефон должен быть уникален в твоей базе.")


def phone_updated(*, t: Translator = _noop_t) -> str:
    return t("✅ Телефон обновлён.")
