from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from src.rate_limiter import RateLimiter


class RateLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_allow_returns_true_when_set_succeeds(self) -> None:
        redis = AsyncMock()
        redis.set = AsyncMock(return_value=True)
        limiter = RateLimiter(redis, prefix="test:rl")

        allowed = await limiter.allow(key="k", ttl_sec=5)

        self.assertTrue(allowed)
        redis.set.assert_awaited_with("k", "1", ex=5, nx=True)

    async def test_allow_returns_false_when_set_is_not_nx(self) -> None:
        redis = AsyncMock()
        redis.set = AsyncMock(return_value=None)
        limiter = RateLimiter(redis, prefix="test:rl")

        allowed = await limiter.allow(key="k", ttl_sec=5)

        self.assertFalse(allowed)

    async def test_allow_is_fail_open_on_redis_errors(self) -> None:
        redis = AsyncMock()
        redis.set = AsyncMock(side_effect=RuntimeError("redis down"))
        limiter = RateLimiter(redis, prefix="test:rl")

        allowed = await limiter.allow(key="k", ttl_sec=5)

        self.assertTrue(allowed)

    async def test_hit_builds_stable_key_from_labels(self) -> None:
        redis = AsyncMock()
        redis.set = AsyncMock(return_value=True)
        limiter = RateLimiter(redis, prefix="test:rl")

        allowed = await limiter.hit(name="master_reg:start", ttl_sec=5, telegram_id=1, other="x")

        self.assertTrue(allowed)
        redis.set.assert_awaited_with(
            "test:rl:master_reg:start:other=x,telegram_id=1",
            "1",
            ex=5,
            nx=True,
        )
