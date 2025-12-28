from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.texts import master_menu as txt

CLIENTS_MENU_CB = {
    "list": "m:clients:list",
    "invite": "m:clients:invite",
    "add": "m:clients:add",
    "search": "m:clients:search",
    "back": "m:clients:back",
}


def build_master_clients_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=txt.CLIENTS_BTN_LIST, callback_data=CLIENTS_MENU_CB["list"]),
                InlineKeyboardButton(text=txt.CLIENTS_BTN_ADD, callback_data=CLIENTS_MENU_CB["add"]),
            ],
            [
                InlineKeyboardButton(text=txt.CLIENTS_BTN_SEARCH, callback_data=CLIENTS_MENU_CB["search"]),
                InlineKeyboardButton(text=txt.CLIENTS_BTN_INVITE, callback_data=CLIENTS_MENU_CB["invite"]),
            ],
            [
                InlineKeyboardButton(text=txt.CLIENTS_BTN_BACK, callback_data=CLIENTS_MENU_CB["back"]),
            ],
        ],
    )


def clients_menu_text() -> str:
    return txt.choose_action()
