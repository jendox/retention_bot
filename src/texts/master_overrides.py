from __future__ import annotations

from datetime import date, time

from src.texts.base import Translator, noop_t as _noop_t


def title(*, t: Translator = _noop_t) -> str:
    return t("🛠 Изменить расписание на день")


def choose_date(*, t: Translator = _noop_t) -> str:
    return t("Выбери дату, для которой нужно изменить расписание:")


def day_summary(*, day: date, window: tuple[time, time] | None, has_override: bool, t: Translator = _noop_t) -> str:
    if window is None:
        window_txt = t("выходной")
    else:
        start, end = window
        window_txt = t(f"{start:%H:%M}–{end:%H:%M}")
    override_txt = t("да") if has_override else t("нет")
    return t(
        f"{title(t=t)}\n\n<b>{day:%d.%m.%Y}</b>\nРабочее время: <b>{window_txt}</b>\nИсключение: <b>{override_txt}</b>",
    )


def prompt_start_time(*, t: Translator = _noop_t) -> str:
    return t("Введи время начала (формат <code>HH:MM</code>):")


def prompt_end_time(*, start_time: time, t: Translator = _noop_t) -> str:
    return t(f"Введи время окончания (формат <code>HH:MM</code>), позже <code>{start_time:%H:%M}</code>:")


def invalid_time(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Не понял время. Пример: <code>09:00</code>")


def invalid_time_order(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Время окончания должно быть позже времени начала.")


def conflicts_title(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Есть записи, которые конфликтуют с новым расписанием:")


def conflicts_hint(*, t: Translator = _noop_t) -> str:
    return t("Разреши их вручную (перенеси/отмени записи) и попробуй снова.")


def btn_day_off(*, t: Translator = _noop_t) -> str:
    return t("🚫 Сделать выходным")


def btn_make_working(*, t: Translator = _noop_t) -> str:
    return t("✅ Сделать рабочим")


def btn_set_hours(*, t: Translator = _noop_t) -> str:
    return t("⏰ Задать часы")


def btn_back_to_schedule(*, t: Translator = _noop_t) -> str:
    return t("◀️ Назад к расписанию")
