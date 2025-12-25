from __future__ import annotations

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from src.observability.events import EventLogger


def _is_not_modified(exc: TelegramBadRequest) -> bool:
    return "message is not modified" in str(exc).lower()


def _is_delete_race(exc: TelegramBadRequest) -> bool:
    text = str(exc).lower()
    return (
        "message to delete not found" in text
        or "message can't be deleted" in text
        or "message cannot be deleted" in text
    )


async def safe_edit_text(
    message,
    *,
    text: str,
    reply_markup=None,
    parse_mode: str | None = None,
    ev: EventLogger | None = None,
    event: str = "ui.edit_text_failed",
) -> bool:
    try:
        await message.edit_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        return True
    except TelegramAPIError as exc:
        logger = ev or EventLogger(__name__)
        if isinstance(exc, TelegramBadRequest) and _is_not_modified(exc):
            logger.debug("ui.edit_not_modified")
            return False
        logger.warning(event, error=str(exc))
        return False


async def safe_edit_reply_markup(
    message,
    *,
    reply_markup=None,
    ev: EventLogger | None = None,
    event: str = "ui.edit_reply_markup_failed",
) -> bool:
    try:
        await message.edit_reply_markup(reply_markup=reply_markup)
        return True
    except TelegramAPIError as exc:
        logger = ev or EventLogger(__name__)
        if isinstance(exc, TelegramBadRequest) and _is_not_modified(exc):
            logger.debug("ui.edit_not_modified")
            return False
        logger.warning(event, error=str(exc))
        return False


async def safe_delete(
    message,
    *,
    ev: EventLogger | None = None,
    event: str = "ui.delete_failed",
) -> bool:
    try:
        await message.delete()
        return True
    except TelegramAPIError as exc:
        logger = ev or EventLogger(__name__)
        if isinstance(exc, TelegramBadRequest) and _is_delete_race(exc):
            logger.debug("ui.delete_skipped", error=str(exc))
            return False
        logger.warning(event, error=str(exc))
        return False


async def safe_bot_edit_message_text(
    bot,
    *,
    chat_id: int,
    message_id: int,
    text: str,
    ev: EventLogger | None = None,
    event: str = "ui.bot_edit_message_text_failed",
    **kwargs,
) -> bool:
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            **kwargs,
        )
        return True
    except TelegramAPIError as exc:
        logger = ev or EventLogger(__name__)
        if isinstance(exc, TelegramBadRequest) and _is_not_modified(exc):
            logger.debug("ui.edit_not_modified")
            return False
        logger.warning(event, error=str(exc))
        return False
