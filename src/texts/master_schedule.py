from __future__ import annotations

from textwrap import dedent

from src.texts.base import Translator, noop_t as _noop_t


def title_today(*, t: Translator = _noop_t) -> str:
    return t("Расписание на сегодня")


def title_tomorrow(*, t: Translator = _noop_t) -> str:
    return t("Расписание на завтра")


def title_week(*, t: Translator = _noop_t) -> str:
    return t("Расписание на неделю")


def title_month(*, t: Translator = _noop_t) -> str:
    return t("Расписание на месяц")


def title_default(*, t: Translator = _noop_t) -> str:
    return t("Расписание")


def btn_today(*, t: Translator = _noop_t) -> str:
    return t("📅 Сегодня")


def btn_tomorrow(*, t: Translator = _noop_t) -> str:
    return t("📆 Завтра")


def btn_week(*, t: Translator = _noop_t) -> str:
    return t("📆 Неделя")


def btn_month(*, t: Translator = _noop_t) -> str:
    return t("🗓 Месяц")


def btn_reschedule(*, t: Translator = _noop_t) -> str:
    return t("🔄 Перенести")


def btn_back_to_schedule(*, t: Translator = _noop_t) -> str:
    return t("◀️ Назад к расписанию")


def choose_period(*, t: Translator = _noop_t) -> str:
    return t("Выбери период, чтобы посмотреть записи:")


def back_to_main_menu(*, t: Translator = _noop_t) -> str:
    return t("Возвращаемся в главное меню.")


def client_fallback(*, client_id: int | str, t: Translator = _noop_t) -> str:
    return t(f"Клиент #{client_id}")


def phone_missing(*, t: Translator = _noop_t) -> str:
    return t("не указан")


def empty(*, title: str, t: Translator = _noop_t) -> str:
    return t(f"{title}\n\nЗдесь пока нет записей 🙂")


def choose_booking(*, title: str, t: Translator = _noop_t) -> str:
    return t(f"{title}\nВыбери запись:")


def no_access(*, t: Translator = _noop_t) -> str:
    return t("Нет доступа к этой записи.")


def card(
    *,
    status_line: str,
    date_line: str,
    time_line: str,
    client_line: str,
    phone_line: str,
    t: Translator = _noop_t,
) -> str:
    return t(
        dedent(f"""
        Запись

        {status_line}
        {date_line}
        {time_line}

        {client_line}
        {phone_line}
        """).strip(),
    )


def navigation_error(*, t: Translator = _noop_t) -> str:
    return t("Ошибка навигации.")


def open_booking_error(*, t: Translator = _noop_t) -> str:
    return t("Ошибка открытия записи.")


def action_error(*, t: Translator = _noop_t) -> str:
    return t("Ошибка действия.")


def cancel_failed(*, t: Translator = _noop_t) -> str:
    return t("Не удалось отменить запись (нет доступа или уже неактуальна).")


def cancelled_ok(*, t: Translator = _noop_t) -> str:
    return t("✅ Запись отменена.")


def unknown_action(*, t: Translator = _noop_t) -> str:
    return t("Неизвестное действие.")
