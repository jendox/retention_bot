import asyncio
import os
from datetime import UTC, datetime

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
from src.observability.heartbeat import heartbeat_key
from src.rate_limiter import RateLimiter
from src.settings import AppSettings, app_settings, get_settings
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


async def _read_heartbeat_ts(redis: Redis, *, worker: str) -> int | None:
    raw = await redis.get(heartbeat_key(worker))
    if not raw:
        return None
    try:
        return int(raw)
    except Exception:
        return None


async def _run_workers_watchdog(*, redis: Redis, admin_alerter: AdminAlerter | None) -> None:
    """
    Periodically checks Redis heartbeat keys written by worker processes and alerts admins on "silence".

    The watchdog is best-effort: Redis errors are logged but do not crash the bot.
    """
    seen_ok: dict[str, bool] = {}

    while True:
        obs = get_settings().observability
        if not obs.workers_watchdog_enabled:
            await asyncio.sleep(5)
            continue

        check_sec = max(5, int(obs.workers_heartbeat_check_sec))
        stale_sec = max(10, int(obs.workers_heartbeat_stale_sec))
        now_utc = datetime.now(UTC)

        for worker in ("reminders", "payments"):
            await _watchdog_check_worker(
                redis=redis,
                admin_alerter=admin_alerter,
                worker=worker,
                now_utc=now_utc,
                stale_sec=stale_sec,
                seen_ok=seen_ok,
            )

        await asyncio.sleep(float(check_sec))


async def _watchdog_check_worker(
    *,
    redis: Redis,
    admin_alerter: AdminAlerter | None,
    worker: str,
    now_utc: datetime,
    stale_sec: int,
    seen_ok: dict[str, bool],
) -> None:
    try:
        last_ts = await _read_heartbeat_ts(redis, worker=worker)
    except Exception as exc:
        await ev.aexception(
            "workers.watchdog.redis_error",
            exc=exc,
            admin_alerter=admin_alerter,
            worker=worker,
        )
        return

    age = None if last_ts is None else now_utc.timestamp() - float(last_ts)
    ok = age is not None and age <= float(stale_sec)
    prev_ok = seen_ok.get(worker)
    if ok:
        if prev_ok is False:
            ev.info("workers.heartbeat_restored", worker=worker)
        seen_ok[worker] = True
        return

    if prev_ok is not False:
        age_sec = None if age is None else int(age)
        await ev.aerror(
            f"workers.{worker}.heartbeat_missing",
            admin_alerter=admin_alerter,
            worker=worker,
            stale_sec=stale_sec,
            last_seen_ts=last_ts,
            age_sec=age_sec,
        )
    seen_ok[worker] = False


def _maybe_start_watchdog(
    *,
    redis: Redis,
    admin_alerter: AdminAlerter,
) -> asyncio.Task[None] | None:
    if not get_settings().observability.workers_watchdog_enabled:
        return None
    return asyncio.create_task(
        _run_workers_watchdog(redis=redis, admin_alerter=admin_alerter),
        name="workers_watchdog",
    )


async def _maybe_alert_invite_policy(*, settings: AppSettings, admin_alerter: AdminAlerter) -> None:
    if settings.security.master_public_registration:
        return
    if settings.security.master_invite_secret is not None:
        return
    ev.error("security.invite_policy_misconfigured", invite_only=False)
    await admin_alerter.notify(
        event="security.invite_policy_misconfigured",
        text=admin_txt.invite_policy_misconfigured(),
        level="WARNING",
        throttle_key="security.invite_policy_misconfigured",
        throttle_sec=60 * 60,
        extra={"invite_only": False},
    )


async def _start_polling(
    *,
    settings: AppSettings,
    bot: Bot,
    dp: Dispatcher,
    notifier: Notifier,
    admin_alerter: AdminAlerter,
    postgres_url: str,
) -> None:
    if settings.express_pay is None:
        async with Database.lifespan(url=postgres_url):
            await dp.start_polling(
                bot,
                notifier=notifier,
                admin_alerter=admin_alerter,
            )
        return

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
    watchdog_task: asyncio.Task[None] | None = None

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
        watchdog_task = _maybe_start_watchdog(redis=redis, admin_alerter=admin_alerter)
        await _maybe_alert_invite_policy(settings=settings, admin_alerter=admin_alerter)
        await _start_polling(
            settings=settings,
            bot=bot,
            dp=dp,
            notifier=notifier,
            admin_alerter=admin_alerter,
            postgres_url=postgres_url,
        )

    except Exception as exc:
        await ev.aexception(
            "app.error",
            exc=exc,
            admin_alerter=admin_alerter,
            error_type=type(exc).__name__,
        )
    finally:
        if watchdog_task is not None:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass
        if redis is not None:
            await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        ev.info("app.shutdown")
