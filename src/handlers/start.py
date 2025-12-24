import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot, F, Router
from aiogram.filters import CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import session_local
from src.handlers.client.client_menu import send_client_main_menu
from src.handlers.client.register import start_client_registration
from src.handlers.master.master_menu import send_master_main_menu
from src.handlers.master.register import start_master_registration
from src.repositories import ClientNotFound, ClientRepository, MasterNotFound, MasterRepository
from src.security.master_invites import decode_master_invite_from_start
from src.settings import get_settings
from src.texts import start as txt
from src.user_context import ActiveRole, UserContextStorage
from src.utils import answer_tracked, cleanup_messages, track_message

router = Router(name=__name__)
logger = logging.getLogger(__name__)

START_BOT_BUCKET = "start_bot"


class RoleStates(StatesGroup):
    choosing_role = State()


def _build_role_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=txt.btn_role_master(), callback_data=f"role:{ActiveRole.MASTER.value}")],
            [InlineKeyboardButton(text=txt.btn_role_client(), callback_data=f"role:{ActiveRole.CLIENT.value}")],
        ],
    )


RoleHandler = Callable[[Bot, int, bool], Awaitable[None]]

ROLE_MENU_MAP: dict[ActiveRole, RoleHandler] = {
    ActiveRole.MASTER: send_master_main_menu,
    ActiveRole.CLIENT: send_client_main_menu,
}


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
) -> None:
    await cleanup_messages(state, message.bot, bucket=START_BOT_BUCKET)
    await track_message(state, message, bucket=START_BOT_BUCKET)
    if command.args == "registration":
        await start_master_registration(message, state, user_ctx_storage=user_ctx_storage)
        return
    if command.args and command.args.startswith("c_"):
        await start_client_registration(message, state, user_ctx_storage, command.args)
        return
    if command.args and command.args.startswith("m_"):
        raw = command.args.removeprefix("m_")
        token = decode_master_invite_from_start(raw) or (raw or None)
        await start_master_registration(message, state, user_ctx_storage=user_ctx_storage, token=token)
        return

    telegram_id = message.from_user.id
    await resolve_role_and_dispatch(
        telegram_id=telegram_id,
        message=message,
        state=state,
        user_ctx_storage=user_ctx_storage,
    )
    await message.delete()


async def resolve_role_and_dispatch(
    *,
    telegram_id: int,
    message: Message,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
) -> None:
    settings = get_settings()
    invite_only_master_reg = bool(
        settings.security.master_invite_secret) and not settings.security.master_public_registration
    async with session_local() as session:
        is_master = True
        is_client = True

        master_repo = MasterRepository(session)
        try:
            await master_repo.get_by_telegram_id(telegram_id)
        except MasterNotFound:
            is_master = False

        client_repo = ClientRepository(session)
        try:
            await client_repo.get_by_telegram_id(telegram_id)
        except ClientNotFound:
            is_client = False

    if is_master and is_client:
        await state.set_state(RoleStates.choosing_role)
        await answer_tracked(
            message,
            state,
            text=txt.choose_role(),
            reply_markup=_build_role_keyboard(),
            bucket=START_BOT_BUCKET,
        )
        return

    if is_master:
        await user_ctx_storage.set_role(telegram_id, ActiveRole.MASTER)
        await ROLE_MENU_MAP[ActiveRole.MASTER](message.bot, telegram_id, is_client)
        return

    if is_client:
        await user_ctx_storage.set_role(telegram_id, ActiveRole.CLIENT)
        await ROLE_MENU_MAP[ActiveRole.CLIENT](message.bot, telegram_id, is_master)
        return

    await answer_tracked(
        message,
        state,
        text=(
            txt.greet_unknown_invite_only(contact=settings.billing.contact)
            if invite_only_master_reg
            else txt.greet_unknown(link=f"https://t.me/{settings.telegram.bot_username}?start=registration")
        ),
        bucket=START_BOT_BUCKET,
    )


@router.callback_query(
    StateFilter(RoleStates.choosing_role),
    F.data.in_([f"role:{ActiveRole.MASTER.value}", f"role:{ActiveRole.CLIENT.value}"]),
)
async def choose_role(callback: CallbackQuery, state: FSMContext, user_ctx_storage: UserContextStorage) -> None:
    telegram_id = callback.from_user.id

    raw = callback.data.split(":", 1)[1]
    try:
        role = ActiveRole(raw)
    except ValueError:
        logger.info("choose_role.error", extra={"telegram_id": telegram_id, "raw_role": raw})
        await state.set_state(RoleStates.choosing_role)
        await answer_tracked(
            callback.message,
            state,
            text=txt.role_not_recognized(),
            reply_markup=_build_role_keyboard(),
            bucket=START_BOT_BUCKET,
        )
        return

    logger.info("choose_role.success", extra={"telegram_id": telegram_id, "role": role})
    await user_ctx_storage.set_role(telegram_id, role)
    await ROLE_MENU_MAP[role](callback.bot, telegram_id, True)

    await cleanup_messages(state, callback.bot, bucket=START_BOT_BUCKET)
    await state.clear()
