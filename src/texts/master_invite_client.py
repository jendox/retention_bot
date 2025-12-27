from __future__ import annotations

from src.texts.base import Translator, noop_t as _noop_t

# ----- Buttons -----


def btn_link_only(*, t: Translator = _noop_t) -> str:
    return t("🔗 Только ссылка")


def btn_friendly(*, t: Translator = _noop_t) -> str:
    return t("💬 Текст (дружелюбный)")


def btn_formal(*, t: Translator = _noop_t) -> str:
    return t("📝 Текст (официальный)")


# ----- Flow / alerts -----


def quota_reached(*, current: int, limit: int | None, t: Translator = _noop_t) -> str:
    return t(
        "Похоже, у тебя закончился лимит клиентов на Free.\n\n"
        f"<b>Клиенты:</b> {current}/{limit}\n\n"
        "Чтобы приглашать больше клиентов — подключи Pro.",
    )


def warn_near_limit(*, current: int, limit: int, t: Translator = _noop_t) -> str:
    return t(
        f"\n\n⚠️ Лимит клиентов на Free почти исчерпан:\n<b>{current}</b> из <b>{limit}</b>.\nВ Pro лимитов нет.",
    )


def invite_created(*, warning: str = "", t: Translator = _noop_t) -> str:
    return t("✅ Готово! Ссылка для клиента создана.\n\n") + t("Что отправить клиенту?") + (warning or "")


def cancelled_hint(*, t: Translator = _noop_t) -> str:
    return t("❌ Отменено. Если нужно — нажми «📨 Пригласить» ещё раз 🙂")


def invalid_format(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Некорректный формат.")


def missing_context(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Что-то пошло не так, попробуй ещё раз.")


def done_copy_prompt(*, t: Translator = _noop_t) -> str:
    return t("✅ Готово ✨ Скопируй и отправь клиенту сообщение ниже 👇")


def render_invite_message(*, kind: str, master_name: str, invite_link: str, t: Translator = _noop_t) -> str:
    if kind == "link":
        return t("🔗 Ссылка для клиента:\n\n") + f"{invite_link}"

    if kind == "friendly":
        return (
            t(f"Привет! 😊 Это {master_name}\n\n")
            + t("Хочу пригласить тебя пользоваться удобной записью через BeautyDesk.\n")
            + t("По ссылке ниже ты сможешь быстро перейти в чат и выбрать время ✨\n")
            + t("Ничего скачивать не нужно — всё работает прямо в Telegram.\n\n")
            + f'<a href="{invite_link}">🔗 Записаться к мастеру</a>\n\n'
            + t("Если будут вопросы — пиши 💛")
        )

    return (
        t("Здравствуйте.\n\n")
        + t(f"Меня зовут {master_name}. Приглашаю вас воспользоваться системой записи BeautyDesk ")
        + t("для удобного согласования времени визита.\n")
        + t("Пожалуйста, перейдите по ссылке ниже, чтобы начать онлайн-запись:\n\n")
        + f'<a href="{invite_link}">🔗 Перейти к записи</a>\n\n'
        + t("Если возникнут вопросы — буду рад помочь.")
    )
