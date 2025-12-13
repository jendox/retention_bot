from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from redis.asyncio import Redis


class ActiveRole(StrEnum):
    MASTER = "master"
    CLIENT = "client"


@dataclass(frozen=True)
class UserContext:
    role: ActiveRole


class UserContextStorage:
    def __init__(self, redis: Redis, *, prefix: str = "beautydesk:userctx", ttl_sec: int = 60 * 60 * 24 * 30) -> None:
        self._redis = redis
        self._prefix = prefix
        self._ttl_sec = ttl_sec

    def _role_key(self, telegram_id: int) -> str:
        return f"{self._prefix}:{telegram_id}:role"

    async def set_role(self, telegram_id: int, role: ActiveRole) -> None:
        await self._redis.set(self._role_key(telegram_id), role.value, ex=self._ttl_sec)

    async def get_role(self, telegram_id: int) -> ActiveRole | None:
        value = await self._redis.get(self._role_key(telegram_id))
        if not value:
            return None
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return ActiveRole(value)

    async def clear_role(self, telegram_id: int) -> None:
        await self._redis.delete(self._role_key(telegram_id))
