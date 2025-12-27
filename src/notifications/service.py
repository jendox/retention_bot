from __future__ import annotations

import hashlib

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup

from src.notifications.context import BookingContext, LimitsContext, ReminderContext
from src.notifications.renderer import RenderedMessage, render
from src.notifications.types import NotificationEvent, RecipientKind
from src.observability.events import EventLogger

ev = EventLogger(__name__)

_CHAT_ID_HASH_LEN = 12


def _hash_chat_id(chat_id: int) -> str:
    return hashlib.sha256(str(int(chat_id)).encode("utf-8")).hexdigest()[:_CHAT_ID_HASH_LEN]


class NotificationService:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def _send_message(
        self,
        *,
        chat_id: int | None,
        message: RenderedMessage,
        notification_event: str,
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
            ev.info(
                "notifications.send_success",
                notification_event=notification_event,
                recipient=recipient,
                chat_id_hash=_hash_chat_id(int(chat_id)),
            )
        except TelegramAPIError as exc:
            ev.warning(
                "notifications.send_failed",
                notification_event=notification_event,
                recipient=recipient,
                chat_id_hash=_hash_chat_id(int(chat_id)),
                error_type=type(exc).__name__,
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
            ev.warning(
                "notifications.empty_message",
                event=event.value,
                recipient=recipient.value,
            )
            return

        await self._send_message(
            chat_id=chat_id,
            message=message,
            notification_event=str(event.value),
            recipient=str(recipient.value),
        )
