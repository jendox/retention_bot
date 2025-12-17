import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from redis.asyncio import Redis

from src.core.sa import Database
from src.handlers import routers
from src.middlewares import LoggingMiddleware, UserContextMiddleware
from src.notifications.notifier import Notifier
from src.notifications.policy import DefaultNotificationPolicy
from src.observability import setup_logging
from src.settings import AppSettings, app_settings
from src.user_context import UserContextStorage

logger = logging.getLogger("retention_bot")


def build_dispatcher(redis: Redis) -> Dispatcher:
    dp = Dispatcher()
    user_ctx_storage = UserContextStorage(redis)
    dp.update.outer_middleware(LoggingMiddleware())
    dp.update.outer_middleware(UserContextMiddleware(user_ctx_storage))
    dp.include_routers(*routers)

    return dp


async def main():
    settings = AppSettings.load()
    app_settings.set(settings)
    debug = settings.debug
    setup_logging(debug=debug)
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
        notifier = Notifier(
            bot=bot,
            policy=DefaultNotificationPolicy(),
        )
        async with Database.lifespan(url=postgres_url):
            await dp.start_polling(bot, notifier=notifier)

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
