import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.redis import RedisEventIsolation, RedisStorage
from redis.asyncio import Redis

from src.core.sa import Database
from src.handlers import routers
from src.middlewares import (
    HandlerLogContextMiddleware,
    LogContextMiddleware,
    LoggingMiddleware,
    RateLimiterMiddleware,
    UserContextMiddleware,
)
from src.notifications.notifier import Notifier
from src.notifications.policy import DefaultNotificationPolicy
from src.observability.alerts import AdminAlerter
from src.observability.errors import global_error_handler
from src.observability import setup_logging
from src.rate_limiter import RateLimiter
from src.settings import AppSettings, app_settings
from src.user_context import UserContextStorage

logger = logging.getLogger("retention_bot")


def build_dispatcher(redis: Redis) -> Dispatcher:
    key_builder = DefaultKeyBuilder(prefix="fsm", with_bot_id=True, with_destiny=True)
    storage = RedisStorage(redis=redis, key_builder=key_builder)
    isolation = RedisEventIsolation(
        redis=redis,
        key_builder=key_builder,
        lock_kwargs={"timeout": 60},
    )
    dp = Dispatcher(storage=storage, events_isolation=isolation)
    user_ctx_storage = UserContextStorage(redis)
    rate_limiter = RateLimiter(redis)
    dp.update.outer_middleware(LogContextMiddleware())
    dp.update.outer_middleware(LoggingMiddleware())
    dp.update.outer_middleware(RateLimiterMiddleware(rate_limiter))
    dp.update.outer_middleware(UserContextMiddleware(user_ctx_storage))
    for name, observer in dp.observers.items():
        if name in {"update", "error", "errors"}:
            continue
        observer.outer_middleware(HandlerLogContextMiddleware())
    dp.include_routers(*routers)

    return dp


async def main():
    settings = AppSettings.load()
    app_settings.set(settings)
    debug = settings.debug
    setup_logging(
        debug=debug,
        service="retention_bot",
        env=os.getenv("APP_ENV") or ("dev" if debug else "prod"),
        version="0.1.0",
    )
    redis: Redis | None = None

    try:
        token = settings.telegram.bot_token.get_secret_value()
        postgres_url = settings.database.postgres_url
        redis_url = settings.database.redis_url

        redis = Redis.from_url(redis_url)

        bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode="HTML"),
        )
        dp = build_dispatcher(redis)
        dp.errors.register(global_error_handler)
        notifier = Notifier(
            bot=bot,
            policy=DefaultNotificationPolicy(),
        )
        admin_alerter = AdminAlerter(bot=bot, admin_ids=settings.admin.telegram_ids, redis=redis)
        async with Database.lifespan(url=postgres_url):
            await dp.start_polling(bot, notifier=notifier, admin_alerter=admin_alerter)

    except Exception as exc:
        logger.error("app.error", exc_info=True, extra={"error_type": type(exc).__name__})
    finally:
        if redis is not None:
            await redis.aclose()


if __name__ == "__main__":
    try:
        logger.info("app.start")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("app.shutdown")
