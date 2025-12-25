from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from src.observability.context import bind_log_context, new_trace_id, reset_log_context, set_log_context
from src.rate_limiter import RateLimiter
from src.user_context import UserContextStorage

logger = logging.getLogger(__name__)


class LogContextMiddleware(BaseMiddleware):
    async def _maybe_get_fsm_state(self, data: dict[str, Any]) -> str | None:
        state = data.get("state")
        if state is None:
            return None
        try:
            return await state.get_state()
        except Exception:
            return None

    async def _build_ctx(
        self,
        *,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        event_ = getattr(event, "event", None)
        from_user = getattr(event_, "from_user", None)
        chat = getattr(event_, "chat", None)
        message = getattr(event_, "message", None)

        ctx: dict[str, Any] = {
            "trace_id": new_trace_id(),
            "update_id": getattr(event, "update_id", None),
            "event_type": type(event_).__name__ if event_ is not None else type(event).__name__,
            "handler": f"{getattr(handler, '__module__', '')}.{getattr(handler, '__name__', '')}".strip("."),
        }
        if from_user is not None:
            ctx["telegram_id"] = from_user.id
            username = getattr(from_user, "username", None)
            if username:
                ctx["telegram_username"] = username
        if chat is not None:
            ctx["chat_id"] = chat.id
        if message is not None:
            ctx["message_id"] = getattr(message, "message_id", None)

        fsm_state = await self._maybe_get_fsm_state(data)
        if fsm_state:
            ctx["fsm_state"] = fsm_state
        return ctx

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        ctx = await self._build_ctx(handler=handler, event=event, data=data)
        token = set_log_context(ctx)
        try:
            return await handler(event, data)
        finally:
            reset_log_context(token)


class HandlerLogContextMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        bind_log_context(
            handler=f"{getattr(handler, '__module__', '')}.{getattr(handler, '__name__', '')}".strip("."),
        )
        return await handler(event, data)


class LoggingMiddleware(BaseMiddleware):
    def __init__(self, *, slow_threshold_ms: int = 1_000) -> None:
        self._slow_threshold_ms = slow_threshold_ms

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        started = time.perf_counter()
        try:
            result = await handler(event, data)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            # Keep one canonical error log in the global error handler; here we only add timing in debug.
            logger.debug(
                "handler.exception",
                extra={"duration_ms": duration_ms, "error_type": type(exc).__name__},
            )
            raise

        duration_ms = int((time.perf_counter() - started) * 1000)
        if duration_ms >= self._slow_threshold_ms:
            logger.warning("handler.slow", extra={"duration_ms": duration_ms})
        else:
            logger.debug("handler.ok", extra={"duration_ms": duration_ms})
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
            bind_log_context(active_role=str(role))

        return await handler(event, data)


class RateLimiterMiddleware(BaseMiddleware):
    def __init__(self, limiter: RateLimiter) -> None:
        self._limiter = limiter

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["rate_limiter"] = self._limiter
        return await handler(event, data)
