import logging

from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.core.sa import session_local
from src.handlers.client.client_menu import send_client_main_menu
from src.handlers.client.register import start_client_registration
from src.handlers.master.master_menu import send_master_main_menu
from src.handlers.master.register import start_master_registration
from src.repositories import ClientNotFound, ClientRepository, MasterNotFound, MasterRepository
from src.utils import answer_tracked

router = Router(name=__name__)
logger = logging.getLogger(__name__)


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    state: FSMContext,
) -> None:
    await message.delete()

    if command.args == "registration":
        logger.debug("process new master")
        await start_master_registration(message, state)
        return
    if command.args and command.args.startswith("c_"):
        logger.debug("process client with invite link")
        await start_client_registration(message, state, command.args)
        return
    if command.args and command.args.startswith("m_"):
        logger.debug("process master with invite link")
        pass

    telegram_id = message.from_user.id
    async with session_local() as session:
        master_repo = MasterRepository(session)
        try:
            await master_repo.get_by_telegram_id(telegram_id)
            await send_master_main_menu(message, state)
            return
        except MasterNotFound:
            pass
        client_repo = ClientRepository(session)
        try:
            await client_repo.get_by_telegram_id(telegram_id)
            await send_client_main_menu(message, state)
            return
        except ClientNotFound:
            pass

    await answer_tracked(
        message,
        state,
        text="Привет! 👋\n"
             "Я BeautyDesk — бот для записи к мастерам.\n\n"
             "Чтобы записаться, возьми ссылку у своего мастера.\n"
             "Если ты мастер и хочешь подключить бота — "
             "<a href='https://t.me/beautydesk_bot?start=registration'>жми сюда</a>.",
    )
