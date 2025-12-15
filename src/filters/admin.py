from __future__ import annotations

import os
from functools import lru_cache

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message


@lru_cache(maxsize=1)
def _admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_TELEGRAM_IDS", "")
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            continue
    return ids


class AdminOnly(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        from_user = getattr(event, "from_user", None)
        if not from_user:
            return False
        return from_user.id in _admin_ids()

