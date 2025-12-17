from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup

from src.notifications.context import BookingContext, LimitsContext, ReminderContext
from src.notifications.renderer import RenderedMessage, render
from src.notifications.types import NotificationEvent, RecipientKind

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def _send_message(
        self,
        *,
        chat_id: int | None,
        message: RenderedMessage,
        event: str,
        recipient: str,
    ) -> None:
        if chat_id is None:
            return

        try:
            await self._bot.send_message(
                chat_id=chat_id,
                text=message.text,
                reply_markup=message.reply_markup,
                parse_mode=message.parse_mode,
            )
            logger.info(
                "notifications.send_success",
                extra={"event": event, "recipient": recipient, "chat_id": chat_id},
            )
        except TelegramAPIError:
            logger.warning(
                "notifications.send_failed",
                extra={"event": event, "recipient": recipient, "chat_id": chat_id},
                exc_info=True,
            )

    async def send(
        self,
        *,
        event: NotificationEvent,
        recipient: RecipientKind,
        chat_id: int | None,
        context: BookingContext | LimitsContext | ReminderContext,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        message = render(event=event, recipient=recipient, context=context, reply_markup=reply_markup)
        if not message.text.strip():
            logger.warning(
                "notifications.empty_message",
                extra={"event": event.value, "recipient": recipient.value},
            )
            return

        await self._send_message(
            chat_id=chat_id,
            message=message,
            event=str(event.value),
            recipient=str(recipient.value),
        )
