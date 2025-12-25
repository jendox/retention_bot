from __future__ import annotations

from html import escape as html_escape

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.core.sa import session_local
from src.handlers.shared.guards import rate_limit_message
from src.handlers.shared.ui import safe_delete
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.rate_limiter import RateLimiter
from src.repositories import ClientNotFound, ClientRepository
from src.texts import client_list_masters as txt
from src.texts.client_messages import CLIENT_NOT_FOUND_MESSAGE

router = Router(name=__name__)
ev = EventLogger(__name__)


async def start_client_list_masters(
    message: Message,
    state: FSMContext,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="client_list_masters", step="start")
    if not await rate_limit_message(message, rate_limiter, name="client_list_masters:start", ttl_sec=2):
        return
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
        lines.append(f"• <b>{html_escape(str(master.name))}</b>")

    await message.answer(text="\n".join(lines), parse_mode="HTML")
    await safe_delete(message, ev=ev, event="client_list_masters.delete_menu_message_failed")
