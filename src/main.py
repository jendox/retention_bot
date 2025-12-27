import asyncio
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.redis import RedisEventIsolation, RedisStorage
from redis.asyncio import Redis

from src.core.sa import Database
from src.handlers import routers
from src.integrations.expresspay import ExpressPayClient
from src.middlewares import (
    HandlerLogContextMiddleware,
    LogContextMiddleware,
    LoggingMiddleware,
    RateLimiterMiddleware,
    UserContextMiddleware,
)
from src.notifications.notifier import Notifier
from src.notifications.policy import DefaultNotificationPolicy
from src.observability import setup_logging
from src.observability.alerts import AdminAlerter
from src.observability.errors import global_error_handler
from src.observability.events import EventLogger
from src.rate_limiter import RateLimiter
from src.settings import AppSettings, app_settings
from src.texts import admin as admin_txt
from src.user_context import UserContextStorage

ev = EventLogger("retention_bot")


def build_dispatcher(redis: Redis, *, slow_threshold_ms: int) -> Dispatcher:
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
    dp.update.outer_middleware(LoggingMiddleware(slow_threshold_ms=slow_threshold_ms))
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
    admin_alerter: AdminAlerter | None = None

    try:
        token = settings.telegram.bot_token.get_secret_value()
        postgres_url = settings.database.postgres_url
        redis_url = settings.database.redis_url

        redis = Redis.from_url(redis_url)

        bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode="HTML"),
        )
        dp = build_dispatcher(redis, slow_threshold_ms=settings.observability.handler_slow_ms)
        dp.errors.register(global_error_handler)
        notifier = Notifier(
            bot=bot,
            policy=DefaultNotificationPolicy(),
        )
        admin_alerter = AdminAlerter(
            bot=bot,
            admin_ids=settings.admin.telegram_ids,
            redis=redis,
            enabled=settings.observability.alerts_enabled,
            default_throttle_sec=settings.observability.alerts_default_throttle_sec,
        )
        if (not settings.security.master_public_registration) and (settings.security.master_invite_secret is None):
            ev.error("security.invite_policy_misconfigured", invite_only=False)
            await admin_alerter.notify(
                event="security.invite_policy_misconfigured",
                text=admin_txt.invite_policy_misconfigured(),
                level="WARNING",
                throttle_key="security.invite_policy_misconfigured",
                throttle_sec=60 * 60,
                extra={"invite_only": False},
            )
        if settings.express_pay is None:
            async with Database.lifespan(url=postgres_url):
                await dp.start_polling(
                    bot,
                    notifier=notifier,
                    admin_alerter=admin_alerter,
                )
        else:
            async with (
                Database.lifespan(url=postgres_url),
                ExpressPayClient(settings.express_pay) as express_pay_client,
            ):
                await dp.start_polling(
                    bot,
                    notifier=notifier,
                    admin_alerter=admin_alerter,
                    express_pay_client=express_pay_client,
                )

    except Exception as exc:
        await ev.aexception(
            "app.error",
            exc=exc,
            admin_alerter=admin_alerter,
            error_type=type(exc).__name__,
        )
    finally:
        if redis is not None:
            await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        ev.info("app.shutdown")
