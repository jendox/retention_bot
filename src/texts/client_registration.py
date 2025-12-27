from __future__ import annotations

from src.texts.base import Translator, noop_t as _noop_t

# ----- Flow texts -----


def ask_name(*, t: Translator = _noop_t) -> str:
    return t(
        "Привет! 👋\nДавай настроим твой профиль клиента в BeautyDesk.\n\nКак тебя зовут? (Например: Маша)",
    )


def name_invalid(*, t: Translator = _noop_t) -> str:
    return t(
        "⚠️ Не понял имя 🤔\nПожалуйста, напиши, как к тебе обращаться. Например: <b>Маша</b>",
    )


def ask_phone(*, name: str, t: Translator = _noop_t) -> str:
    return t(f"Отлично, <b>{name}</b>! ✨\n\nДобавь свой номер телефона (375...):")


def phone_invalid(*, t: Translator = _noop_t) -> str:
    return t(
        "⚠️ Не смог разобрать телефонный номер 🤔\n\n"
        "Пожалуйста, введи реальный номер в формате 375291234567, "
        "чтобы мастер мог с тобой связаться:",
    )


def confirm_details(*, name: str, phone: str, t: Translator = _noop_t) -> str:
    return t(
        f"Проверь, пожалуйста, данные 👇\n\n<b>Имя:</b> {name}\n<b>Номер телефона:</b> {phone}\nВсё верно?",
    )


def creating_profile(*, t: Translator = _noop_t) -> str:
    return t("⏳ Создаю профиль клиента…\nПожалуйста, подожди несколько секунд.")


def state_broken_alert(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Что-то пошло не так, попробуй ещё раз.")


def done(*, t: Translator = _noop_t) -> str:
    return t(
        "🎉 Готово!\n\nТвой профиль клиента создан.\nТеперь ты можешь управлять записями в BeautyDesk.",
    )


def confirm_out_of_state(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Эта кнопка уже не активна.")


def cancel_alert(*, t: Translator = _noop_t) -> str:
    return t("❌ Регистрация отменена.")


# ----- Error texts -----


def err_invite_inactive(*, t: Translator = _noop_t) -> str:
    return t(
        "😕 Эта ссылка на регистрацию больше не активна.\n"
        "Она могла истечь или быть использована ранее.\n\n"
        "Попроси мастера отправить новую ссылку ✨",
    )


def err_invite_wrong_link(*, t: Translator = _noop_t) -> str:
    return t(
        "😕 Эта ссылка не подходит для регистрации клиента.\n\n"
        "Попроси мастера прислать актуальную ссылку на регистрацию ✨",
    )


def err_quota_exceeded(*, t: Translator = _noop_t) -> str:
    return t(
        "🚫 Похоже, у мастера закончился лимит клиентов на Free.\n\nПопроси мастера подключить Pro или попробуй позже.",
    )


def err_phone_conflict(*, t: Translator = _noop_t) -> str:
    return t(
        "⚠️ Не получилось подключиться по ссылке.\n"
        "Похоже, у мастера уже есть клиент с таким телефоном.\n\n"
        "Попроси мастера помочь подключиться.",
    )


def err_generic(*, t: Translator = _noop_t) -> str:
    return t(
        "⚠️ Не получилось зарегистрироваться по ссылке.\n\nПопробуй ещё раз или попроси мастера прислать новую ссылку.",
    )
