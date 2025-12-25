import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from src.filters.user_role import UserRole
from src.handlers.client.booking import start_client_add_booking
from src.handlers.client.list_bookings import start_client_list_bookings
from src.handlers.client.list_masters import start_client_list_masters
from src.handlers.client.settings import open_client_settings
from src.rate_limiter import RateLimiter
from src.texts import client_menu as txt, common as common_txt
from src.user_context import ActiveRole, UserContextStorage

logger = logging.getLogger(__name__)
router = Router(name=__name__)


def build_client_main_keyboard(show_switch_role: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=txt.MENU_BOOK)],
        [
            KeyboardButton(text=txt.MENU_BOOKINGS),
            KeyboardButton(text=txt.MENU_MASTERS),
        ],
        [KeyboardButton(text=txt.MENU_SETTINGS)],
    ]
    if show_switch_role:
        rows.append([KeyboardButton(text=txt.MENU_SWITCH_ROLE)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder=common_txt.input_choose_action(),
    )


async def send_client_main_menu(
    bot: Bot,
    chat_id: int,
    show_switch_role: bool = False,
) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=txt.MAIN_MENU_TEXT,
        reply_markup=build_client_main_keyboard(show_switch_role),
    )


@router.message(UserRole(ActiveRole.CLIENT), F.text == txt.MENU_BOOK)
async def client_add_booking(message: Message, state: FSMContext, rate_limiter: RateLimiter | None = None) -> None:
    await start_client_add_booking(message, state, rate_limiter)


@router.message(UserRole(ActiveRole.CLIENT), F.text == txt.MENU_BOOKINGS)
async def client_list_bookings(message: Message, state: FSMContext, rate_limiter: RateLimiter | None = None) -> None:
    await start_client_list_bookings(message, state, rate_limiter)


@router.message(UserRole(ActiveRole.CLIENT), F.text == txt.MENU_MASTERS)
async def client_list_masters(message: Message, state: FSMContext, rate_limiter: RateLimiter | None = None) -> None:
    await start_client_list_masters(message, state, rate_limiter)


@router.message(UserRole(ActiveRole.CLIENT), F.text == txt.MENU_SWITCH_ROLE)
async def client_switch_role(
    message: Message,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
) -> None:
    from src.handlers.master.master_menu import send_master_main_menu

    telegram_id = message.from_user.id
    await user_ctx_storage.set_role(telegram_id, ActiveRole.MASTER)
    await state.clear()
    await send_master_main_menu(message.bot, telegram_id, show_switch_role=True)
    await message.delete()


@router.message(UserRole(ActiveRole.CLIENT), F.text == txt.MENU_SETTINGS)
async def client_settings(message: Message, state: FSMContext, rate_limiter: RateLimiter | None = None) -> None:
    await state.clear()
    await open_client_settings(message, state, rate_limiter)
