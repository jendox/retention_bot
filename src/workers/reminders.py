from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.sa import Database, session_local
from src.datetime_utils import to_zone
from src.models import Booking as BookingEntity
from src.notifications.context import ReminderContext
from src.notifications.notifier import NotificationRequest, Notifier
from src.notifications.policy import DefaultNotificationPolicy, NotificationFacts
from src.notifications.types import NotificationEvent, RecipientKind
from src.observability import setup_logging
from src.observability.events import EventLogger
from src.schemas.enums import BookingStatus
from src.settings import AppSettings, app_settings
from src.use_cases.entitlements import EntitlementsService

ev = EventLogger("workers.reminders")


@dataclass(frozen=True)
class ReminderKind:
    event: NotificationEvent
    offset: timedelta
    name: str


REMINDERS: tuple[ReminderKind, ...] = (
    ReminderKind(event=NotificationEvent.REMINDER_24H, offset=timedelta(hours=24), name="24h"),
    ReminderKind(event=NotificationEvent.REMINDER_2H, offset=timedelta(hours=2), name="2h"),
)


def dedup_key(*, booking_id: int, start_at_utc: datetime, kind: ReminderKind) -> str:
    start_ts = int(start_at_utc.timestamp())
    return f"beautydesk:reminder:{kind.name}:{booking_id}:{start_ts}"


def due_window(*, now_utc: datetime, kind: ReminderKind, tick: timedelta) -> tuple[datetime, datetime]:
    """
    For reminder kind with offset D:
    remind_at = booking.start_at - D
    remind_at in [now, now+tick) <=> booking.start_at in [now+D, now+D+tick)
    """
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware (UTC).")
    start = now_utc + kind.offset
    end = start + tick
    return start, end


async def _dedup_allow(redis: Redis, *, key: str, ttl: timedelta) -> bool:
    try:
        ok = await redis.set(name=key, value="1", ex=int(ttl.total_seconds()), nx=True)
        return bool(ok)
    except Exception:
        ev.warning("reminders.dedup_redis_error", key=key)
        return True


async def _load_due_bookings(*, start_at: datetime, end_at: datetime) -> list[BookingEntity]:
    async with session_local() as session:
        stmt = (
            select(BookingEntity)
            .where(
                BookingEntity.start_at >= start_at,
                BookingEntity.start_at < end_at,
                BookingEntity.status == BookingStatus.CONFIRMED,
            )
            .options(
                selectinload(BookingEntity.master),
                selectinload(BookingEntity.client),
            )
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def _send_reminder(  # noqa: PLR0913
    *,
    notifier: Notifier,
    redis: Redis,
    booking: BookingEntity,
    kind: ReminderKind,
    now_utc: datetime,
    dedup_ttl: timedelta,
    plan_cache: dict[int, bool],
) -> bool:
    client = booking.client
    master = booking.master

    client_telegram_id = getattr(client, "telegram_id", None)
    if client_telegram_id is None:
        return False
    if not getattr(client, "notifications_enabled", True):
        return False
    if not getattr(master, "notify_clients", True):
        return False

    start_at_utc = booking.start_at.astimezone(UTC)
    key = dedup_key(booking_id=int(booking.id), start_at_utc=start_at_utc, kind=kind)
    if not await _dedup_allow(redis, key=key, ttl=dedup_ttl):
        return False

    master_id = int(booking.master_id)
    plan_is_pro = plan_cache.get(master_id)
    if plan_is_pro is None:
        async with session_local() as session:
            plan_is_pro = bool((await EntitlementsService(session).get_plan(master_id=master_id)).is_pro)
        plan_cache[master_id] = plan_is_pro

    slot_client = to_zone(start_at_utc, client.timezone)
    slot_str = slot_client.strftime("%d.%m.%Y %H:%M")

    return await notifier.maybe_send(
        NotificationRequest(
            event=kind.event,
            recipient=RecipientKind.CLIENT,
            chat_id=int(client_telegram_id),
            context=ReminderContext(
                master_name=str(master.name),
                slot_str=slot_str,
            ),
            facts=NotificationFacts(
                event=kind.event,
                recipient=RecipientKind.CLIENT,
                chat_id=int(client_telegram_id),
                plan_is_pro=bool(plan_is_pro),
                master_notify_clients=bool(getattr(master, "notify_clients", True)),
                client_notifications_enabled=bool(getattr(client, "notifications_enabled", True)),
                booking_start_at_utc=start_at_utc,
                now_utc=now_utc,
            ),
        ),
    )


async def run_loop(
    *,
    redis: Redis,
    notifier: Notifier,
    tick_sec: int,
    window_sec: int,
) -> None:
    tick = timedelta(seconds=int(tick_sec))
    window = timedelta(seconds=int(window_sec))

    while True:
        now_utc = datetime.now(UTC)
        sent = 0
        candidates = 0
        plan_cache: dict[int, bool] = {}

        for kind in REMINDERS:
            start_at, end_at = due_window(now_utc=now_utc, kind=kind, tick=window)
            bookings = await _load_due_bookings(start_at=start_at, end_at=end_at)
            candidates += len(bookings)
            for booking in bookings:
                try:
                    ok = await _send_reminder(
                        notifier=notifier,
                        redis=redis,
                        booking=booking,
                        kind=kind,
                        now_utc=now_utc,
                        dedup_ttl=timedelta(days=7),
                        plan_cache=plan_cache,
                    )
                    sent += int(ok)
                except Exception as exc:
                    await ev.aexception(
                        "reminders.send_failed",
                        exc=exc,
                        booking_id=getattr(booking, "id", None),
                        master_id=getattr(booking, "master_id", None),
                        client_id=getattr(booking, "client_id", None),
                        kind=kind.name,
                    )

        ev.info(
            "reminders.tick",
            sent=sent,
            candidates=candidates,
            tick_sec=int(tick.total_seconds()),
            window_sec=int(window.total_seconds()),
        )
        await asyncio.sleep(float(tick.total_seconds()))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BeautyDesk reminder worker (Pro-only client reminders).")
    parser.add_argument("--env-file", default=None, help="Env file path (default: ENV_FILE or .env.local)")
    parser.add_argument("--tick-sec", type=int, default=int(os.getenv("REMINDERS_TICK_SEC", "30")))
    parser.add_argument("--window-sec", type=int, default=int(os.getenv("REMINDERS_WINDOW_SEC", "60")))
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    settings = AppSettings.load(env_file=args.env_file)
    app_settings.set(settings)

    setup_logging(
        debug=bool(settings.debug),
        service="retention_bot",
        env=os.getenv("APP_ENV") or ("dev" if settings.debug else "prod"),
        version="0.1.0",
    )

    redis = Redis.from_url(settings.database.redis_url)
    bot = Bot(
        token=settings.telegram.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    notifier = Notifier(
        bot=bot,
        policy=DefaultNotificationPolicy(),
    )

    try:
        async with Database.lifespan(url=settings.database.postgres_url):
            await run_loop(
                redis=redis,
                notifier=notifier,
                tick_sec=int(args.tick_sec),
                window_sec=int(args.window_sec),
            )
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
