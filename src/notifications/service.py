from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup

from src.notifications.context import BookingContext
from src.notifications.renderer import render
from src.notifications.types import NotificationEvent, RecipientKind

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_booking(
        self,
        *,
        event: NotificationEvent,
        recipient: RecipientKind,
        chat_id: int | None,
        context: BookingContext,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        if chat_id is None:
            return

        msg = render(event=event, recipient=recipient, context=context, reply_markup=reply_markup)
        try:
            await self._bot.send_message(
                chat_id=chat_id,
                text=msg.text,
                reply_markup=msg.reply_markup,
                parse_mode=msg.parse_mode,
            )
        except TelegramAPIError:
            logger.warning(
                "notifications.send_failed",
                extra={"event": str(event), "recipient": str(recipient), "chat_id": chat_id},
                exc_info=True,
            )

