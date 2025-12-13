import logging
from textwrap import dedent

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from src.filters.user_role import UserRole
from src.handlers.client.client_menu import send_client_main_menu
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


def build_master_main_keyboard(show_switch_role: bool) -> ReplyKeyboardMarkup:
    rows = [
        [
            KeyboardButton(text="📨 Пригласить клиента"),
            KeyboardButton(text="➕ Добавить клиента"),
        ],
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


async def send_master_main_menu(
    message: Message,
    show_switch_role: bool = False,
) -> None:
    await message.answer(
        text=MASTER_MAIN_MENU_TEXT,
        reply_markup=build_master_main_keyboard(show_switch_role),
    )


@router.message(UserRole(ActiveRole.MASTER), F.text == "🔄 Сменить роль")
async def master_switch_role(
    message: Message,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
) -> None:
    await user_ctx_storage.set_role(message.from_user.id, ActiveRole.CLIENT)
    await state.clear()
    await send_client_main_menu(message, show_switch_role=True)


@router.message(F.text == "➕ Добавить клиента")
async def master_add_client(message: Message, state: FSMContext) -> None:
    from src.handlers.master.add_client import start_add_client

    await start_add_client(message, state)


@router.message(F.text == "🗓 Добавить запись")
async def master_add_booking(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        "Тут будет создание новой записи 📆",
    )


@router.message(F.text == "📅 Расписание")
async def master_schedule(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        "Здесь в будущем покажем твое расписание на день / неделю 🗓",
    )


@router.message(UserRole(ActiveRole.MASTER), F.text == "⚙️ Настройки")
async def master_settings(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        text="Тут будут настройки мастера: график, таймзона, уведомления и т.д. ⚙️",
    )
