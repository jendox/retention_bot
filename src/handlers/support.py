from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.handlers.shared.support_contact import send_support_contact
from src.observability.context import bind_log_context
from src.observability.events import EventLogger

router = Router(name=__name__)
ev = EventLogger(__name__)


@router.message(Command("support"))
async def support_command(message: Message) -> None:
    bind_log_context(flow="support", step="command")
    if message.from_user is None:
        return
    ev.info("support.command", telegram_id=int(message.from_user.id))
    await send_support_contact(bot=message.bot, chat_id=int(message.from_user.id))
