from __future__ import annotations

import logging
from typing import Any

from aiogram.exceptions import TelegramAPIError
from aiogram.types import ErrorEvent

from src.observability.alerts import AdminAlerter

logger = logging.getLogger(__name__)


def _extract_update_context(event: ErrorEvent) -> dict[str, Any]:
    update = event.update
    payload: dict[str, Any] = {
        "update_id": getattr(update, "update_id", None),
    }

    update_event = getattr(update, "event", None)
    payload["event_type"] = type(update_event).__name__ if update_event is not None else type(update).__name__

    from_user = getattr(update_event, "from_user", None)
    if from_user is not None:
        payload["telegram_id"] = from_user.id
        if getattr(from_user, "username", None):
            payload["telegram_username"] = from_user.username

    chat = getattr(update_event, "chat", None)
    if chat is not None:
        payload["chat_id"] = chat.id

    message = getattr(update_event, "message", None)
    if message is not None:
        payload["message_id"] = getattr(message, "message_id", None)

    return payload


async def global_error_handler(event: ErrorEvent, admin_alerter: AdminAlerter | None = None) -> None:
    exc = event.exception
    extra = _extract_update_context(event)
    extra["error_type"] = type(exc).__name__

    # One structured error log per unhandled exception (handler middleware may log separately).
    logger.error("bot.unhandled_exception", exc_info=True, extra=extra)

    if admin_alerter is None:
        return

    # Telegram API errors can be noisy; keep them in logs, but avoid paging admins by default.
    if isinstance(exc, TelegramAPIError):
        return

    await admin_alerter.notify(
        event="bot.unhandled_exception",
        text=f"{type(exc).__name__}: {exc}",
        level="ERROR",
        throttle_key=f"bot.unhandled_exception:{type(exc).__name__}",
        extra=extra,
    )

