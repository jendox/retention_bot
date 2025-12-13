from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from src.user_context import ActiveRole


class UserRole(BaseFilter):
    def __init__(self, role: ActiveRole):
        self._role = role

    async def __call__(
        self,
        event: Message | CallbackQuery,
        active_role: ActiveRole | None = None,
    ) -> bool:
        return active_role == self._role
