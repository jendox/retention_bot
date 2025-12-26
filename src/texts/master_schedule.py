from __future__ import annotations

from src.schemas.enums import AttendanceOutcome
from src.texts.base import Translator, noop_t as _noop_t


def title_today(*, t: Translator = _noop_t) -> str:
    return t("Расписание на сегодня")


def title_tomorrow(*, t: Translator = _noop_t) -> str:
    return t("Расписание на завтра")


def title_week(*, t: Translator = _noop_t) -> str:
    return t("Расписание на неделю")


def title_month(*, t: Translator = _noop_t) -> str:
    return t("Расписание на месяц")


def title_yesterday(*, t: Translator = _noop_t) -> str:
    return t("Расписание за вчера")


def title_history_week(*, t: Translator = _noop_t) -> str:
    return t("Расписание за последние 7 дней")


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


def btn_yesterday(*, t: Translator = _noop_t) -> str:
    return t("📅 Вчера")


def btn_history_week(*, t: Translator = _noop_t) -> str:
    return t("🕘 7 дней (история)")


def btn_override_day(*, t: Translator = _noop_t) -> str:
    return t("🛠 Изменить день")


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


def card(*, lines: list[str], t: Translator = _noop_t) -> str:
    details = "\n".join(lines)
    return t(f"<b>Запись</b>\n\n{details}")


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


def cancel_confirm_prompt(*, t: Translator = _noop_t) -> str:
    return t("⚠️ Точно отменить запись?")


def btn_cancel_yes(*, t: Translator = _noop_t) -> str:
    return t("✅ Да, отменить")


def btn_cancel_no(*, t: Translator = _noop_t) -> str:
    return t("◀️ Не отменять")


def unknown_action(*, t: Translator = _noop_t) -> str:
    return t("Неизвестное действие.")


def attendance_label(*, outcome: AttendanceOutcome, t: Translator = _noop_t) -> str:
    if outcome == AttendanceOutcome.ATTENDED:
        return t("✅ Пришёл")
    if outcome == AttendanceOutcome.NO_SHOW:
        return t("❌ Не пришёл")
    return t("— не отмечено")


def attendance_line(*, outcome: AttendanceOutcome, t: Translator = _noop_t) -> str:
    return t(f"📌 Посещение: {attendance_label(outcome=outcome, t=t)}")


def btn_mark_attended(*, t: Translator = _noop_t) -> str:
    return t("✅ Пришёл")


def btn_mark_no_show(*, t: Translator = _noop_t) -> str:
    return t("❌ Не пришёл")


def attendance_marked(*, t: Translator = _noop_t) -> str:
    return t("Отмечено.")


def attendance_already_marked(*, t: Translator = _noop_t) -> str:
    return t("Посещение уже отмечено.")


def attendance_not_eligible(*, t: Translator = _noop_t) -> str:
    return t("Можно отмечать посещение только для прошедших подтверждённых записей.")


def attendance_failed(*, t: Translator = _noop_t) -> str:
    return t("Не удалось отметить посещение. Попробуй ещё раз.")
