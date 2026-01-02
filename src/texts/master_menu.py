from __future__ import annotations

from textwrap import dedent

MENU_ADD_BOOKING = "🗓 Добавить запись"
MENU_SCHEDULE = "📅 Расписание"
MENU_CLIENTS = "👥 Клиенты"
MENU_SETTINGS = "⚙️ Настройки"
MENU_SWITCH_ROLE = "🔄 Сменить роль"

CLIENTS_BTN_LIST = "📋 Список"
CLIENTS_BTN_ADD = "➕ Добавить"
CLIENTS_BTN_SEARCH = "🔍 Найти"
CLIENTS_BTN_INVITE = "📨 Пригласить"
CLIENTS_BTN_BACK = "✖️ Закрыть"

MAIN_MENU_TEXT = dedent("""
    Главное меню мастера 💇‍♀️
    Начнём с 3 приглашений клиентам 👇
""").strip()


def choose_action() -> str:
    return "Выбери действие:"


def back_to_main_menu() -> str:
    return "ℹ️ Возвращаемся в главное меню."
