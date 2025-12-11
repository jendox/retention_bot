import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

from src.core.sa import Database
from src.handlers import routers

logger = logging.getLogger("retention_bot")


async def main():
    load_dotenv(".env.local")
    debug = os.getenv("DEBUG", "false").lower() == "true"
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    try:
        token = os.environ["TELEGRAM__BOT_TOKEN"]
        postgres_url = os.environ["DATABASE__POSTGRES_URL"]

        dp = Dispatcher()
        bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode="HTML"),
        )

        dp.include_routers(*routers)
        async with Database.lifespan(url=postgres_url):
            await dp.start_polling(bot)

    except Exception as exc:
        logger.error("app.error", exc_info=True, extra={"error": str(exc)})


if __name__ == "__main__":
    try:
        logger.info("app.start")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("app.shutdown")
