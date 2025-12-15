from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from src.user_context import UserContextStorage

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseMiddleware):
    def __init__(self, *, slow_threshold_ms: int = 1_000) -> None:
        self._slow_threshold_ms = slow_threshold_ms

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        event_ = getattr(event, "event", None)
        from_user = getattr(event_, "from_user", None)
        chat = getattr(event_, "chat", None)

        extra = {
            "update_id": getattr(event, "update_id", None),
            "event_type": type(event_).__name__ if event_ is not None else type(event).__name__,
            "handler": f"{handler.__module__}",
        }
        if from_user is not None:
            extra["telegram_id"] = from_user.id
        if chat is not None:
            extra["chat_id"] = chat.id

        active_role = data.get("active_role")
        if active_role is not None:
            extra["active_role"] = str(active_role)

        started = time.perf_counter()
        try:
            result = await handler(event, data)
        except Exception:
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.error("handler.error", exc_info=True, extra={**extra, "duration_ms": duration_ms})
            raise

        duration_ms = int((time.perf_counter() - started) * 1000)
        if duration_ms >= self._slow_threshold_ms:
            logger.warning("handler.slow", extra={**extra, "duration_ms": duration_ms})
        else:
            logger.debug("handler.ok", extra={**extra, "duration_ms": duration_ms})
        return result


class UserContextMiddleware(BaseMiddleware):
    def __init__(self, storage: UserContextStorage) -> None:
        self._storage = storage

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        event_ = getattr(event, "event", None)
        from_user = getattr(event_, "from_user", None)
        if from_user:
            role = await self._storage.get_role(from_user.id)
            data["active_role"] = role
            data["user_ctx_storage"] = self._storage

        return await handler(event, data)
