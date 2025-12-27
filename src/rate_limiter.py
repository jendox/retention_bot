from __future__ import annotations

import hashlib

from redis.asyncio import Redis

from src.observability.events import EventLogger

ev = EventLogger(__name__)

_KEY_HASH_LEN = 12


def _hash_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:_KEY_HASH_LEN]


class RateLimiter:
    """
    Minimal Redis-backed rate limiter.

    Uses an atomic `SET key value NX EX ttl` to ensure at most 1 hit per key per TTL window.
    Designed to be fail-open: if Redis errors occur, the request is allowed and a warning is logged.
    """

    def __init__(self, redis: Redis, *, prefix: str = "beautydesk:rl") -> None:
        self._redis = redis
        self._prefix = prefix

    def key(self, name: str, *parts: object) -> str:
        suffix = ":".join(str(p) for p in parts)
        if suffix:
            return f"{self._prefix}:{name}:{suffix}"
        return f"{self._prefix}:{name}"

    async def allow(self, *, key: str, ttl_sec: int) -> bool:
        try:
            return bool(await self._redis.set(key, "1", ex=int(ttl_sec), nx=True))
        except Exception as exc:
            ev.warning(
                "rate_limit.redis_error",
                key_hash=_hash_key(key),
                error_type=type(exc).__name__,
            )
            return True

    async def hit(self, *, name: str, ttl_sec: int, **labels: object) -> bool:
        """
        Convenience wrapper that builds a namespaced key from labels.

        Example: hit(name="master_reg:start", telegram_id=123, ttl_sec=5)
        -> key "beautydesk:rl:master_reg:start:telegram_id=123"
        """
        label_part = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        key = self.key(name, label_part) if label_part else self.key(name)
        return await self.allow(key=key, ttl_sec=ttl_sec)
