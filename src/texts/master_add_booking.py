from __future__ import annotations

from datetime import date

from src.texts.base import Translator, noop_t as _noop_t
from src.texts.common import label_offline_badge


def label_offline(*, t: Translator = _noop_t) -> str:
    return label_offline_badge(t=t)


def ask_query(*, t: Translator = _noop_t) -> str:
    return t("Введи имя или телефон клиента, чтобы создать запись:")


def no_clients(*, t: Translator = _noop_t) -> str:
    return t("Пока нет ни одного клиента. Сначала добавь клиента, чтобы создать запись.")


def query_required(*, t: Translator = _noop_t) -> str:
    return t("Нужно ввести имя или телефон клиента.")


def no_matches(*, t: Translator = _noop_t) -> str:
    return t("Не нашёл клиентов по запросу. Попробуй другое имя или телефон.")


def choose_client(*, t: Translator = _noop_t) -> str:
    return t("Выбери клиента:")


def choose_date(*, t: Translator = _noop_t) -> str:
    return t("Выбери дату для записи:")


def date_out_of_range(*, today: date, max_date: date, t: Translator = _noop_t) -> str:
    return t(
        f"Можно выбрать дату с {today.strftime('%d.%m.%Y')} "
        f"по {max_date.strftime('%d.%m.%Y')}",
    )


def no_slots(*, t: Translator = _noop_t) -> str:
    return t("На этот день свободных слотов нет. Выбери другую дату.")


def slots_title(*, day: date, t: Translator = _noop_t) -> str:
    return t(f"Свободные слоты на {day.strftime('%d.%m.%Y')}:")


def confirm_booking(*, client_name: str, slot_str: str, t: Translator = _noop_t) -> str:
    return t(
        "Подтверди запись:\n\n"
        f"Клиент: {client_name}\n"
        f"Дата/время: {slot_str}",
    )


def quota_reached(*, t: Translator = _noop_t) -> str:
    return t("Лимит записей на Free исчерпан. Подключи Pro, чтобы создавать больше записей.")


def warn_near_limit(*, new_count: int, limit: int, t: Translator = _noop_t) -> str:
    return t(
        "⚠️ Лимит записей на Free почти исчерпан.\n\n"
        f"<b>{new_count}</b> из <b>{limit}</b> записей в этом месяце.\n"
        "В Pro лимитов нет.",
    )


def slot_taken(*, t: Translator = _noop_t) -> str:
    return t("Упс — этот слот только что заняли 😕\nПожалуйста, выбери другое время.")


def created(*, client_has_tg: bool, t: Translator = _noop_t) -> str:
    return t("✅ Запись создана") + (t(" (📵 оффлайн)") if not client_has_tg else "")


def cancel_alert(*, t: Translator = _noop_t) -> str:
    return t("❌ Создание записи отменено")
