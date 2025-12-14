import logging
from textwrap import dedent

from aiogram import F, Router
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
from src.handlers.master.invite_client import start_invite_client
from src.handlers.master.list_clients import start_clients_entry
from src.handlers.master.schedule import master_schedule
from src.user_context import ActiveRole, UserContextStorage
from src.utils import answer_tracked

logger = logging.getLogger(__name__)
router = Router(name=__name__)

MASTER_MAIN_MENU_TEXT = dedent("""
    Главное меню мастера 💇‍♀️
    Здесь ты можешь:
    • приглашать и добавлять клиентов
    • создавать записи
    • смотреть расписание
    • управлять настройками
""").strip()

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
            KeyboardButton(text="🗓 Добавить запись"),
            KeyboardButton(text="📅 Расписание"),
        ],
        [
            KeyboardButton(text="👥 Клиенты"),
            KeyboardButton(text="⚙️ Настройки"),
        ],
    ]
    if show_switch_role:
        rows.append([KeyboardButton(text="🔄 Сменить роль")])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def build_master_clients_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 Список", callback_data=CLIENTS_MENU_CB["list"]),
                InlineKeyboardButton(text="➕ Добавить", callback_data=CLIENTS_MENU_CB["add"]),
            ],
            [
                InlineKeyboardButton(text="🔎 Найти/Изменить", callback_data=CLIENTS_MENU_CB["search"]),
                InlineKeyboardButton(text="📨 Пригласить", callback_data=CLIENTS_MENU_CB["invite"]),
            ],
            [
                InlineKeyboardButton(text="◀️ Назад", callback_data=CLIENTS_MENU_CB["back"]),
            ],
        ],
    )


async def send_master_main_menu(
    message: Message,
    show_switch_role: bool = False,
) -> None:
    await message.answer(
        text=MASTER_MAIN_MENU_TEXT,
        reply_markup=build_master_main_keyboard(show_switch_role),
    )


@router.message(UserRole(ActiveRole.MASTER), F.text == "👥 Клиенты")
async def master_clients(message: Message, state: FSMContext) -> None:
    await message.answer(
        text="Выбери действие:",
        reply_markup=build_master_clients_keyboard(),
    )
    await message.delete()


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == CLIENTS_MENU_CB["list"])
async def master_clients_list(callback: CallbackQuery) -> None:
    await callback.answer()
    await start_clients_entry(callback)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == CLIENTS_MENU_CB["add"])
async def master_add_client(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await start_add_client(callback, state)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == CLIENTS_MENU_CB["search"])
async def master_search_client(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Здесь будет добавлен поиск и редактирование клиентов.")


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == CLIENTS_MENU_CB["invite"])
async def master_invite_client(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await start_invite_client(callback, state)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == CLIENTS_MENU_CB["back"])
async def master_back_to_main_menu(callback: CallbackQuery) -> None:
    await callback.answer("Возвращаемся в главное меню.")
    await callback.message.delete()


@router.message(UserRole(ActiveRole.MASTER), F.text == "🔄 Сменить роль")
async def master_switch_role(
    message: Message,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
) -> None:
    from src.handlers.client.client_menu import send_client_main_menu

    await user_ctx_storage.set_role(message.from_user.id, ActiveRole.CLIENT)
    await state.clear()
    await send_client_main_menu(message, show_switch_role=True)
    await message.delete()


@router.message(UserRole(ActiveRole.MASTER), F.text == "🗓 Добавить запись")
async def master_add_booking(message: Message, state: FSMContext) -> None:
    await start_add_booking(message, state)


@router.message(UserRole(ActiveRole.MASTER), F.text == "📅 Расписание")
async def master_schedule_entry(message: Message) -> None:
    await master_schedule(message)


@router.message(UserRole(ActiveRole.MASTER), F.text == "⚙️ Настройки")
async def master_settings(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        text="Тут будут настройки мастера: график, таймзона, уведомления и т.д. ⚙️",
    )
    await message.delete()
