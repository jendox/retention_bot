import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv
from redis.asyncio import Redis

from src.core.sa import Database
from src.handlers import routers
from src.middlewares import UserContextMiddleware
from src.user_context import UserContextStorage

logger = logging.getLogger("retention_bot")


def build_dispatcher(redis: Redis) -> Dispatcher:
    dp = Dispatcher()
    user_ctx_storage = UserContextStorage(redis)
    dp.update.outer_middleware(UserContextMiddleware(user_ctx_storage))
    dp.include_routers(*routers)

    return dp


async def main():
    load_dotenv(".env.local")
    debug = os.getenv("DEBUG", "false").lower() == "true"
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    redis: Redis | None = None

    try:
        token = os.environ["TELEGRAM__BOT_TOKEN"]
        postgres_url = os.environ["DATABASE__POSTGRES_URL"]
        redis_url = os.environ["DATABASE__REDIS_URL"]

        redis = Redis.from_url(redis_url)

        bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode="HTML"),
        )
        dp = build_dispatcher(redis)
        async with Database.lifespan(url=postgres_url):
            await dp.start_polling(bot)

    except Exception as exc:
        logger.error("app.error", exc_info=True, extra={"error": str(exc)})
    finally:
        if redis is not None:
            await redis.aclose()


if __name__ == "__main__":
    try:
        logger.info("app.start")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("app.shutdown")
