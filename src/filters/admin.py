from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from src.settings import get_settings


class AdminOnly(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        from_user = getattr(event, "from_user", None)
        if not from_user:
            return False
        settings = get_settings()
        return from_user.id in settings.admin.telegram_ids
