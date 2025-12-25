from __future__ import annotations

from aiogram import html

from src.texts.base import Translator, noop_t as _noop_t


def ask_name(*, t: Translator = _noop_t) -> str:
    return t(
        "Привет! 👋\n"
        "Давай настроим твой профиль мастера в BeautyDesk.\n\n"
        "Как тебя зовут? (Например: Мария)",
    )


def name_not_recognized(*, t: Translator = _noop_t) -> str:
    return t(
        "Я не понял имя 🤔\n"
        "Пожалуйста, напиши, как к тебе обращаться. Например: <b>Мария</b>",
    )


def name_too_long(*, max_len: int, t: Translator = _noop_t) -> str:
    return t(
        "Имя слишком длинное 🤔\n"
        f"Пожалуйста, введи имя короче (до <code>{max_len}</code> символов).",
    )


def ask_phone(*, name: str, t: Translator = _noop_t) -> str:
    return t(
        f"Отлично, <b>{html.quote(name)}</b>! ✨\n\n"
        "Теперь добавь номер телефона для связи (в формате <code>375291234567</code>):",
    )


def phone_not_recognized(*, t: Translator = _noop_t) -> str:
    return t(
        "Не смог разобрать номер 🤔\n\n"
        "Пожалуйста, введи реальный номер в формате <code>375291234567</code>:",
    )


def ask_work_days(*, t: Translator = _noop_t) -> str:
    return t(
        "Принято! ✅\n\n"
        "Теперь давай настроим твои рабочие дни.\n\n"
        "<b>В какие дни недели ты работаешь?</b>\n"
        "Напиши номера дней недели:\n"
        "1 — Пн, 2 — Вт, 3 — Ср, 4 — Чт, 5 — Пт, 6 — Сб, 7 — Вс\n\n"
        "Примеры:\n"
        "• <code>1-5</code> — с понедельника по пятницу\n"
        "• <code>1,3,5</code> — пн, ср, пт",
    )


def work_days_not_recognized(*, t: Translator = _noop_t) -> str:
    return t(
        "Не смог разобрать дни недели 🤔\n\n"
        "Напиши номера дней недели в одном из форматов:\n"
        "• <code>1-5</code>\n"
        "• <code>1,3,5</code>\n\n"
        "Где 1 — Пн, 7 — Вс.",
    )


def ask_work_time(*, t: Translator = _noop_t) -> str:
    return t(
        "Принято! ✅\n\n"
        "<b>Твоё рабочее время в течение дня?</b>\n"
        "Напиши в формате <code>H:MM-H:MM</code>.\n"
        "Можно использовать тире <code>-</code>, <code>–</code> или <code>—</code>.\n\n"
        "Также можно написать просто часы — тогда минуты будут <code>:00</code>.\n\n"
        "Примеры: <code>9:00-18:00</code>, <code>10:00–19:00</code>, <code>10-19</code>",
    )


def work_time_not_recognized(*, t: Translator = _noop_t) -> str:
    return t(
        "Не получилось разобрать время 🕒\n\n"
        "Напиши, пожалуйста, в формате <code>H:MM-H:MM</code>.\n"
        "Можно использовать тире <code>-</code>, <code>–</code> или <code>—</code>.\n\n"
        "Можно писать просто часы — например <code>10-19</code>.\n\n"
        "Примеры: <code>9:00-18:00</code>, <code>10:00–19:00</code>, <code>10-19</code>",
    )


def ask_slot_size(*, t: Translator = _noop_t) -> str:
    return t(
        "Супер! ✅\n\n"
        "<b>Какой длительности обычно одна запись?</b>\n"
        "Напиши количество минут (кратно <code>5</code>).\n\n"
        "Например: <code>30</code>, <code>45</code>, <code>60</code>.",
    )


def slot_size_not_recognized(*, t: Translator = _noop_t) -> str:
    return t(
        "Хмм, не похоже на подходящую длительность слота ⏱️\n\n"
        "Напиши количество минут (кратно <code>5</code>), например: <code>30</code>, <code>45</code>, <code>60</code>.",
    )


def confirm(
    *,
    name: str,
    phone: str,
    work_days: str,
    work_time: str,
    slot_size_min: int,
    t: Translator = _noop_t,
) -> str:
    return t(
        "Проверь, пожалуйста, данные 👇\n\n"
        f"<b>Имя:</b> {html.quote(name)}\n"
        f"<b>Телефон:</b> {html.quote(phone)}\n"
        f"<b>Рабочие дни:</b> {html.quote(work_days)}\n"
        f"<b>Время работы:</b> {html.quote(work_time)}\n"
        f"<b>Длительность записи:</b> {html.quote(slot_size_min)} мин.\n\n"
        "Всё верно?",
    )


def creating_profile(*, t: Translator = _noop_t) -> str:
    return t("⏳ Создаю профиль мастера…\nПожалуйста, подожди несколько секунд.")


def done(*, t: Translator = _noop_t) -> str:
    return t(
        "Готово! 🎉\n\n"
        "Твой профиль мастера создан.\n"
        "Теперь ты можешь принимать клиентов и вести записи в BeautyDesk.",
    )


def broken_state_retry(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Что-то пошло не так. Попробуй зарегистрироваться заново.")


def invite_required(*, contact: str, t: Translator = _noop_t) -> str:
    return t(
        "⚠️ Регистрация мастера доступна только по пригласительной ссылке.\n\n"
        "Напиши сюда — и мы пришлём ссылку: "
        f"{html.quote(contact)}",
    )


def invite_invalid(*, contact: str, t: Translator = _noop_t) -> str:
    return t(
        "⚠️ Похоже, ссылка для регистрации устарела или уже не работает.\n\n"
        "Напиши сюда — и мы пришлём новую: "
        f"{html.quote(contact)}",
    )
