import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

from src.utils import answer_tracked

logger = logging.getLogger(__name__)
router = Router(name=__name__)

CLIENT_MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Записаться")],
        [
            KeyboardButton(text="📋 Мои записи"),
            KeyboardButton(text="💇‍♀️ Мои мастера"),
        ],
        [KeyboardButton(text="⚙️ Настройки")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Выберите действие",
)


async def send_client_main_menu(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        text=(
            "Главное меню клиента 💛\n\n"
            "Здесь ты можешь:\n"
            "• записаться к мастеру\n"
            "• посмотреть и управлять своими записями\n"
            "• увидеть список мастеров\n"
            "• настроить профиль\n"
        ),
        reply_markup=CLIENT_MAIN_KEYBOARD,
    )


@router.message(F.text == "➕ Записаться")
async def client_add_booking(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        "Здесь скоро можно будет выбрать мастера и записаться на удобное время ✨",
    )


@router.message(F.text == "📋 Мои записи")
async def client_list_bookings(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        "Тут в будущем покажем список твоих записей с возможностью отмены 🗓",
    )


@router.message(F.text == "💇‍♀️ Мои мастера")
async def client_list_masters(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        "Здесь появится список мастеров, к которым ты уже записывался(лась) 💇‍♀️",
    )


@router.message(F.text == "⚙️ Настройки")
async def client_settings(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        "Тут позже добавим настройки профиля и уведомлений ⚙️",
    )
