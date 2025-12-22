from __future__ import annotations

from datetime import date

from src.texts.base import Translator, noop_t as _noop_t


def pro_only(*, t: Translator = _noop_t) -> str:
    return t("Перенос записи доступен в Pro.")


def not_your_booking(*, t: Translator = _noop_t) -> str:
    return t("Это не твоя запись.")


def not_reschedulable(*, t: Translator = _noop_t) -> str:
    return t("Эту запись нельзя перенести.")


def past_booking(*, t: Translator = _noop_t) -> str:
    return t("Нельзя переносить прошедшие записи.")


def choose_new_date(*, t: Translator = _noop_t) -> str:
    return t("Выбери новую дату для записи:")


def broken_state(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Что-то пошло не так, попробуй ещё раз.")


def date_out_of_range(*, today: date, max_day: date, t: Translator = _noop_t) -> str:
    return t(
        f"Можно выбрать дату с {today.strftime('%d.%m.%Y')} "
        f"по {max_day.strftime('%d.%m.%Y')}",
    )


def no_slots(*, t: Translator = _noop_t) -> str:
    return t("На этот день свободных слотов нет. Выбери другую дату.")


def slots_title(*, day: date, t: Translator = _noop_t) -> str:
    return t(f"Свободные слоты на {day.strftime('%d.%m.%Y')}:")


def confirm(*, client_name: str, day: str, time_str: str, t: Translator = _noop_t) -> str:
    return t(
        "Подтверди перенос записи:\n\n"
        f"👤 {client_name}\n"
        f"📅 {day}\n"
        f"⏰ {time_str}",
    )


def slot_taken(*, t: Translator = _noop_t) -> str:
    return t("Упс — этот слот только что заняли 😕\nПожалуйста, выбери другое время.")


def update_failed(*, t: Translator = _noop_t) -> str:
    return t("Не удалось перенести запись.")


def updated(*, t: Translator = _noop_t) -> str:
    return t("Запись перенесена ✅")


def cancelled(*, t: Translator = _noop_t) -> str:
    return t("Перенос отменён.")


def client_fallback(*, t: Translator = _noop_t) -> str:
    return t("клиент")
