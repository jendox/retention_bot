import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from src.filters.user_role import UserRole
from src.handlers.master.add_booking import start_add_booking
from src.handlers.master.add_client import start_add_client
from src.handlers.master.edit_client import start_edit_client
from src.handlers.master.invite_client import start_invite_client
from src.handlers.master.list_clients import start_clients_entry
from src.handlers.master.schedule import master_schedule
from src.handlers.master.settings import open_master_settings
from src.notifications.notifier import Notifier
from src.texts import common as common_txt, master_menu as txt
from src.user_context import ActiveRole, UserContextStorage

logger = logging.getLogger(__name__)
router = Router(name=__name__)

CLIENTS_MENU_CB = {
    "list": "m:clients:list",
    "invite": "m:clients:invite",
    "add": "m:clients:add",
    "search": "m:clients:search",
    "back": "m:clients:back",
}


def build_master_main_keyboard(show_switch_role: bool) -> ReplyKeyboardMarkup:
    rows = [
        [
            KeyboardButton(text=txt.MENU_ADD_BOOKING),
            KeyboardButton(text=txt.MENU_SCHEDULE),
        ],
        [
            KeyboardButton(text=txt.MENU_CLIENTS),
            KeyboardButton(text=txt.MENU_SETTINGS),
        ],
    ]
    if show_switch_role:
        rows.append([KeyboardButton(text=txt.MENU_SWITCH_ROLE)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder=common_txt.input_choose_action(),
    )


def build_master_clients_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=txt.CLIENTS_BTN_LIST, callback_data=CLIENTS_MENU_CB["list"]),
                InlineKeyboardButton(text=txt.CLIENTS_BTN_ADD, callback_data=CLIENTS_MENU_CB["add"]),
            ],
            [
                InlineKeyboardButton(text=txt.CLIENTS_BTN_SEARCH_EDIT, callback_data=CLIENTS_MENU_CB["search"]),
                InlineKeyboardButton(text=txt.CLIENTS_BTN_INVITE, callback_data=CLIENTS_MENU_CB["invite"]),
            ],
            [
                InlineKeyboardButton(text=txt.CLIENTS_BTN_BACK, callback_data=CLIENTS_MENU_CB["back"]),
            ],
        ],
    )


async def send_master_main_menu(
    bot: Bot,
    chat_id: int,
    show_switch_role: bool = False,
) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=txt.MAIN_MENU_TEXT,
        reply_markup=build_master_main_keyboard(show_switch_role),
    )


@router.message(UserRole(ActiveRole.MASTER), F.text == txt.MENU_CLIENTS)
async def master_clients(message: Message, state: FSMContext) -> None:
    await message.answer(
        text=txt.choose_action(),
        reply_markup=build_master_clients_keyboard(),
    )
    await message.delete()


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == CLIENTS_MENU_CB["list"])
async def master_clients_list(callback: CallbackQuery) -> None:
    await callback.answer()
    await start_clients_entry(callback)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == CLIENTS_MENU_CB["add"])
async def master_add_client(callback: CallbackQuery, state: FSMContext, notifier: Notifier) -> None:
    await callback.answer()
    await start_add_client(callback, state, notifier)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == CLIENTS_MENU_CB["search"])
async def master_search_client(callback: CallbackQuery, state: FSMContext) -> None:
    await start_edit_client(callback, state)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == CLIENTS_MENU_CB["invite"])
async def master_invite_client(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await start_invite_client(callback, state)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == CLIENTS_MENU_CB["back"])
async def master_back_to_main_menu(callback: CallbackQuery) -> None:
    await callback.answer(txt.back_to_main_menu())
    await callback.message.delete()


@router.message(UserRole(ActiveRole.MASTER), F.text == txt.MENU_SWITCH_ROLE)
async def master_switch_role(
    message: Message,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
) -> None:
    from src.handlers.client.client_menu import send_client_main_menu

    telegram_id = message.from_user.id
    await user_ctx_storage.set_role(telegram_id, ActiveRole.CLIENT)
    await state.clear()
    await send_client_main_menu(message.bot, telegram_id, show_switch_role=True)
    await message.delete()


@router.message(UserRole(ActiveRole.MASTER), F.text == txt.MENU_ADD_BOOKING)
async def master_add_booking(message: Message, state: FSMContext) -> None:
    await start_add_booking(message, state)


@router.message(UserRole(ActiveRole.MASTER), F.text == txt.MENU_SCHEDULE)
async def master_schedule_entry(message: Message) -> None:
    await master_schedule(message)
    await message.delete()


@router.message(UserRole(ActiveRole.MASTER), F.text == txt.MENU_SETTINGS)
async def master_settings(message: Message, state: FSMContext) -> None:
    await open_master_settings(message, state)
    await message.delete()
