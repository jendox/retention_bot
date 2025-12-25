from __future__ import annotations

from datetime import date, datetime

from src.texts.base import Translator, noop_t as _noop_t


def no_masters(*, t: Translator = _noop_t) -> str:
    return t(
        "У тебя пока нет подключенных мастеров 👀\n"
        "Попроси мастера прислать ссылку для записи в BeautyDesk.",
    )


def choose_master(*, t: Translator = _noop_t) -> str:
    return t("Выбери мастера, к которому хочешь записаться 💇‍♀️")


def choose_date(*, t: Translator = _noop_t) -> str:
    return t("Выбери дату для записи 📅")


def choose_time(*, client_day: date, t: Translator = _noop_t) -> str:
    return t(f"Свободные слоты на {client_day.strftime('%d.%m.%Y')} ⏰\n"
             "Выбери удобное время:")


def state_broken_alert(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Что-то пошло не так, попробуй ещё раз.")


def available_dates(*, min_date: date, max_date: date, t: Translator = _noop_t) -> str:
    return t(
        f"Можно выбрать дату с {min_date.strftime('%d.%m.%Y')} "
        f"по {max_date.strftime('%d.%m.%Y')}",
    )


def no_available_slots(*, t: Translator = _noop_t) -> str:
    return t("ℹ️ На этот день свободных слотов нет 😕\nПопробуй выбрать другую дату.")


def incorrect_slot(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Некорректный слот, попробуй ещё раз.")


def confirm_details(*, slot_dt_client: datetime, t: Translator = _noop_t) -> str:
    return t(
        f"Подтверди запись 👇\n\n"
        f"<b>Дата:</b> {slot_dt_client.strftime('%d.%m.%Y')}\n"
        f"<b>Время:</b> {slot_dt_client.strftime('%H:%M')}\n",
    )


def booking_not_saved(*, t: Translator = _noop_t) -> str:
    return t("ℹ️ Запись не сохранена.")


def booking_cancelled(*, t: Translator = _noop_t) -> str:
    return t("❌ Запись отменена. Если передумаешь — просто нажми «➕ Записаться» 🙂")


def booking_limit_reached(*, t: Translator = _noop_t) -> str:
    return t(
        "🚫 Сейчас у мастера временно ограничено количество онлайн-записей.\n"
        "Попроси мастера подключить Pro или попробуй позже.",
    )


def slot_not_available(*, t: Translator = _noop_t) -> str:
    return t(
        "⚠️ Упс — этот слот только что заняли 😕\n"
        "Пожалуйста, выбери другое время.",
    )


def done(*, t: Translator = _noop_t) -> str:
    return t(
        "🎉 Готово!\n\n"
        "Запись создана и отправлена мастеру на подтверждение.\n"
        "Статус можно посмотреть в разделе «📋 Мои записи».\n"
        "Если у мастера подключён Pro и включены уведомления — я дополнительно сообщу.\n\n"
        "Чтобы записаться ещё раз — нажми «➕ Записаться».",
    )
