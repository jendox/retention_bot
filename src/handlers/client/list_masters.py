import logging

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.core.sa import session_local
from src.repositories import ClientNotFound, ClientRepository
from src.texts import client_list_masters as txt
from src.texts.client_messages import CLIENT_NOT_FOUND_MESSAGE

logger = logging.getLogger(__name__)
router = Router(name=__name__)


async def start_client_list_masters(message: Message, state: FSMContext) -> None:
    telegram_id = message.from_user.id
    async with session_local() as session:
        repo = ClientRepository(session)
        try:
            client = await repo.get_details_by_telegram_id(telegram_id)
        except ClientNotFound:
            await message.answer(CLIENT_NOT_FOUND_MESSAGE)
            return

    masters = client.masters
    if not masters:
        await message.answer(
            text=txt.empty(),
        )
        return

    lines = [txt.title()]
    for master in masters:
        lines.append(f"• <b>{master.name}</b>")

    await message.answer(text="\n".join(lines))
