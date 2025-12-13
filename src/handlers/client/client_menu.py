import logging
from textwrap import dedent

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from src.filters.user_role import UserRole
from src.handlers.master.master_menu import send_master_main_menu
from src.user_context import ActiveRole, UserContextStorage
from src.utils import answer_tracked

logger = logging.getLogger(__name__)
router = Router(name=__name__)

CLIENT_MAIN_MENU_TEXT = dedent("""
    Главное меню клиента 💛
    Здесь ты можешь:
    • записаться к мастеру
    • посмотреть и управлять своими записями
    • увидеть список мастеров
    • настроить профиль
""").strip()


def build_client_main_keyboard(show_switch_role: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="➕ Записаться")],
        [
            KeyboardButton(text="📋 Мои записи"),
            KeyboardButton(text="💇‍♀️ Мои мастера"),
        ],
        [KeyboardButton(text="⚙️ Настройки")],
    ]
    if show_switch_role:
        rows.append([KeyboardButton(text="🔄 Сменить роль")])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


async def send_client_main_menu(
    message: Message,
    show_switch_role: bool = False,
) -> None:
    await message.answer(
        text=CLIENT_MAIN_MENU_TEXT,
        reply_markup=build_client_main_keyboard(show_switch_role),
    )


@router.message(UserRole(ActiveRole.CLIENT), F.text == "🔄 Сменить роль")
async def client_switch_role(
    message: Message,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
) -> None:
    await user_ctx_storage.set_role(message.from_user.id, ActiveRole.MASTER)
    await state.clear()
    await send_master_main_menu(message, show_switch_role=True)


@router.message(UserRole(ActiveRole.CLIENT), F.text == "⚙️ Настройки")
async def client_settings(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        text="Тут позже добавим настройки профиля и уведомлений ⚙️",
    )
