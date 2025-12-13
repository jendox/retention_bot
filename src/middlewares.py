from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from src.user_context import UserContextStorage


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
