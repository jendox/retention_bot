from __future__ import annotations

from textwrap import dedent

MENU_ADD_BOOKING = "🗓 Добавить запись"
MENU_SCHEDULE = "📅 Расписание"
MENU_CLIENTS = "👥 Клиенты"
MENU_SETTINGS = "⚙️ Настройки"
MENU_SWITCH_ROLE = "🔄 Сменить роль"

CLIENTS_BTN_LIST = "📋 Список"
CLIENTS_BTN_ADD = "➕ Добавить"
CLIENTS_BTN_SEARCH_EDIT = "🔎 Найти/Изменить"
CLIENTS_BTN_INVITE = "📨 Пригласить"
CLIENTS_BTN_BACK = "◀️ Назад"

MAIN_MENU_TEXT = dedent("""
    Главное меню мастера 💇‍♀️
    Здесь ты можешь:
    • приглашать и добавлять клиентов
    • создавать записи
    • смотреть расписание
    • управлять настройками
""").strip()


def choose_action() -> str:
    return "Выбери действие:"


def back_to_main_menu() -> str:
    return "ℹ️ Возвращаемся в главное меню."
