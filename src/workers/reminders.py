from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.sa import Database, active_session, session_local
from src.datetime_utils import to_zone
from src.models import Booking as BookingEntity, Master as MasterEntity, Subscription as SubscriptionEntity
from src.notifications.context import BookingContext, OnboardingContext, ReminderContext, SubscriptionContext
from src.notifications.notifier import NotificationRequest, Notifier
from src.notifications.policy import DefaultNotificationPolicy, NotificationFacts
from src.notifications.types import NotificationEvent, RecipientKind
from src.observability import setup_logging
from src.observability.events import EventLogger
from src.observability.heartbeat import write_worker_heartbeat
from src.observability.metrics_server import start_metrics_server
from src.repositories.scheduled_notification import ScheduledNotificationJob, ScheduledNotificationRepository
from src.schemas.enums import AttendanceOutcome, BookingStatus
from src.settings import AppSettings, app_settings, get_settings
from src.texts.buttons import btn_cancel_booking
from src.use_cases.entitlements import EntitlementsService

ev = EventLogger("workers.reminders")

_ONBOARDING_CLIENT_CHOICE_SEQUENCE = 2


@dataclass(frozen=True)
class ReminderKind:
    event: NotificationEvent
    offset: timedelta
    name: str


REMINDERS: tuple[ReminderKind, ...] = (
    ReminderKind(event=NotificationEvent.REMINDER_24H, offset=timedelta(hours=24), name="24h"),
    ReminderKind(event=NotificationEvent.REMINDER_2H, offset=timedelta(hours=2), name="2h"),
)

_CLIENT_BOOKING_EVENTS: set[NotificationEvent] = {
    NotificationEvent.BOOKING_CONFIRMED,
    NotificationEvent.BOOKING_DECLINED,
    NotificationEvent.BOOKING_CREATED_CONFIRMED,
    NotificationEvent.BOOKING_CANCELLED_BY_MASTER,
    NotificationEvent.BOOKING_RESCHEDULED_BY_MASTER,
}

_CLIENT_REMINDER_EVENTS: set[NotificationEvent] = {
    NotificationEvent.REMINDER_24H,
    NotificationEvent.REMINDER_2H,
}

_TRIAL_SUBSCRIPTION_EVENTS: set[NotificationEvent] = {
    NotificationEvent.TRIAL_EXPIRING_D3,
    NotificationEvent.TRIAL_EXPIRING_D1,
    NotificationEvent.TRIAL_EXPIRING_D0,
}

_PRO_SUBSCRIPTION_EVENTS: set[NotificationEvent] = {
    NotificationEvent.PRO_EXPIRING_D5,
    NotificationEvent.PRO_EXPIRING_D2,
    NotificationEvent.PRO_EXPIRING_D0,
    NotificationEvent.PRO_EXPIRED_RECOVERY_D1,
}


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


def _attendance_keyboard(*, booking_id: int) -> InlineKeyboardMarkup:
    open_card_cb = f"m:b:{int(booking_id)}:s:history_week:p:1"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Пришёл", callback_data=f"m:att_rem:attended:{int(booking_id)}"),
                InlineKeyboardButton(text="❌ Не пришёл", callback_data=f"m:att_rem:no_show:{int(booking_id)}"),
            ],
            [
                InlineKeyboardButton(
                    text="⏳ Напомнить через 3 часа",
                    callback_data=f"m:att_rem:snooze3h:{int(booking_id)}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗓 Напомнить завтра в 10:00",
                    callback_data=f"m:att_rem:tomorrow10:{int(booking_id)}",
                ),
            ],
            [InlineKeyboardButton(text="🚫 Не напоминать", callback_data=f"m:att_rem:disable:{int(booking_id)}")],
            [InlineKeyboardButton(text="📄 Открыть запись", callback_data=open_card_cb)],
        ],
    )


def _onboarding_keyboard(*, event: NotificationEvent, sequence: int | None) -> InlineKeyboardMarkup:
    disable = InlineKeyboardButton(text="🔕 Не напоминать", callback_data="m:onb:disable")
    add_client = InlineKeyboardButton(text="➕ Добавить клиента", callback_data="m:onb:add_client")
    invite = InlineKeyboardButton(text="📩 Пригласить в Telegram", callback_data="m:onb:invite_client")
    add_offline = InlineKeyboardButton(text="📵 Добавить офлайн", callback_data="m:onb:add_client")
    add_booking = InlineKeyboardButton(text="➕ Добавить запись", callback_data="m:onb:add_booking")

    if event == NotificationEvent.MASTER_ONBOARDING_ADD_FIRST_CLIENT:
        if int(sequence or 1) == _ONBOARDING_CLIENT_CHOICE_SEQUENCE:
            return InlineKeyboardMarkup(inline_keyboard=[[invite], [add_offline], [disable]])
        return InlineKeyboardMarkup(inline_keyboard=[[add_client], [disable]])

    if event == NotificationEvent.MASTER_ONBOARDING_ADD_FIRST_BOOKING:
        return InlineKeyboardMarkup(inline_keyboard=[[add_booking], [disable]])

    return InlineKeyboardMarkup(inline_keyboard=[[disable]])


async def _load_booking_for_notification(*, booking_id: int) -> BookingEntity | None:
    async with session_local() as session:
        stmt = (
            select(BookingEntity)
            .where(BookingEntity.id == int(booking_id))
            .options(
                selectinload(BookingEntity.master),
                selectinload(BookingEntity.client),
            )
        )
        return await session.scalar(stmt)


async def _load_master_for_notification(*, master_id: int) -> MasterEntity | None:
    async with session_local() as session:
        stmt = select(MasterEntity).where(MasterEntity.id == int(master_id))
        return await session.scalar(stmt)


async def _plan_is_pro(*, master_id: int, cache: dict[int, bool]) -> bool:
    cached = cache.get(master_id)
    if cached is not None:
        return bool(cached)
    async with session_local() as session:
        value = bool((await EntitlementsService(session).get_plan(master_id=master_id)).is_pro)
    cache[master_id] = value
    return value


async def _load_subscription_for_master(*, master_id: int) -> SubscriptionEntity | None:
    async with session_local() as session:
        stmt = select(SubscriptionEntity).where(SubscriptionEntity.master_id == int(master_id))
        return await session.scalar(stmt)


def _pro_upgrade_keyboard(*, renew: bool) -> InlineKeyboardMarkup:
    text = "💎 Продлить Pro" if renew else "💎 Подключить Pro"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data="billing:pro:start")]],
    )


def _ends_on_str(day) -> str:
    return day.strftime("%d.%m.%Y")


@dataclass(frozen=True)
class _SubscriptionSendCtx:
    notifier: Notifier
    master: MasterEntity
    subscription: SubscriptionEntity
    job: ScheduledNotificationJob
    chat_id: int
    now_utc: datetime
    plan_cache: dict[int, bool]


async def _send_trial_subscription_job(
    ctx: _SubscriptionSendCtx,
    event: NotificationEvent,
) -> bool | None:
    subscription = ctx.subscription
    master = ctx.master
    if subscription.trial_until is None or subscription.trial_until <= ctx.now_utc:
        return False
    if subscription.paid_until is not None and subscription.paid_until > ctx.now_utc:
        return False

    local_due = to_zone(ctx.job.due_at, master.timezone).date()
    expiry_day = to_zone(subscription.trial_until, master.timezone).date()
    expected_due_day = {
        NotificationEvent.TRIAL_EXPIRING_D3: expiry_day - timedelta(days=3),
        NotificationEvent.TRIAL_EXPIRING_D1: expiry_day - timedelta(days=1),
        NotificationEvent.TRIAL_EXPIRING_D0: expiry_day,
    }[event]
    if local_due != expected_due_day:
        return False

    plan_is_pro = await _plan_is_pro(master_id=int(master.id), cache=ctx.plan_cache)
    days_left = max(0, int((expiry_day - to_zone(ctx.now_utc, master.timezone).date()).days))

    try:
        return await ctx.notifier.maybe_send(
            NotificationRequest(
                event=event,
                recipient=RecipientKind.MASTER,
                chat_id=int(ctx.chat_id),
                context=SubscriptionContext(
                    master_name=str(master.name),
                    plan="trial",
                    ends_on=_ends_on_str(expiry_day),
                    days_left=days_left,
                ),
                reply_markup=_pro_upgrade_keyboard(renew=False),
                facts=NotificationFacts(
                    event=event,
                    recipient=RecipientKind.MASTER,
                    chat_id=int(ctx.chat_id),
                    plan_is_pro=bool(plan_is_pro),
                ),
            ),
        )
    except Exception:
        return None


async def _send_pro_subscription_job(  # noqa: C901, PLR0911
    ctx: _SubscriptionSendCtx,
    event: NotificationEvent,
) -> bool | None:
    subscription = ctx.subscription
    master = ctx.master
    if subscription.paid_until is None:
        return False
    paid_until = subscription.paid_until

    local_due = to_zone(ctx.job.due_at, master.timezone).date()
    expiry_day = to_zone(paid_until, master.timezone).date()
    expected_due_day = {
        NotificationEvent.PRO_EXPIRING_D5: expiry_day - timedelta(days=5),
        NotificationEvent.PRO_EXPIRING_D2: expiry_day - timedelta(days=2),
        NotificationEvent.PRO_EXPIRING_D0: expiry_day,
        NotificationEvent.PRO_EXPIRED_RECOVERY_D1: expiry_day + timedelta(days=1),
    }[event]
    if local_due != expected_due_day:
        return False

    if event == NotificationEvent.PRO_EXPIRED_RECOVERY_D1:
        if paid_until > ctx.now_utc:
            return False
        if subscription.trial_until is not None and subscription.trial_until > ctx.now_utc:
            return False
    elif paid_until <= ctx.now_utc:
        return False

    plan_is_pro = await _plan_is_pro(master_id=int(master.id), cache=ctx.plan_cache)
    days_left = max(0, int((expiry_day - to_zone(ctx.now_utc, master.timezone).date()).days))

    try:
        return await ctx.notifier.maybe_send(
            NotificationRequest(
                event=event,
                recipient=RecipientKind.MASTER,
                chat_id=int(ctx.chat_id),
                context=SubscriptionContext(
                    master_name=str(master.name),
                    plan="pro",
                    ends_on=_ends_on_str(expiry_day),
                    days_left=days_left,
                ),
                reply_markup=_pro_upgrade_keyboard(renew=True),
                facts=NotificationFacts(
                    event=event,
                    recipient=RecipientKind.MASTER,
                    chat_id=int(ctx.chat_id),
                    plan_is_pro=bool(plan_is_pro),
                ),
            ),
        )
    except Exception:
        return None


async def _send_subscription_job(
    *,
    notifier: Notifier,
    event: NotificationEvent,
    job: ScheduledNotificationJob,
    chat_id: int,
    now_utc: datetime,
    plan_cache: dict[int, bool],
) -> bool | None:
    if job.master_id is None:
        return False
    master = await _load_master_for_notification(master_id=int(job.master_id))
    if master is None:
        return False
    subscription = await _load_subscription_for_master(master_id=int(master.id))
    if subscription is None:
        return False

    ctx = _SubscriptionSendCtx(
        notifier=notifier,
        master=master,
        subscription=subscription,
        job=job,
        chat_id=chat_id,
        now_utc=now_utc,
        plan_cache=plan_cache,
    )
    if event in _TRIAL_SUBSCRIPTION_EVENTS:
        return await _send_trial_subscription_job(ctx, event)
    if event in _PRO_SUBSCRIPTION_EVENTS:
        return await _send_pro_subscription_job(ctx, event)

    return False


async def _send_client_reminder(
    *,
    notifier: Notifier,
    event: NotificationEvent,
    booking: BookingEntity,
    now_utc: datetime,
    plan_is_pro: bool,
) -> bool | None:
    if booking.status != BookingStatus.CONFIRMED:
        return False

    client = booking.client
    master = booking.master

    if getattr(client, "telegram_id", None) is None:
        return False
    if not getattr(client, "notifications_enabled", True):
        return False
    if not getattr(master, "notify_clients", True):
        return False

    start_at_utc = booking.start_at.astimezone(UTC)
    slot_client = to_zone(start_at_utc, client.timezone)
    slot_str = slot_client.strftime("%d.%m.%Y %H:%M")
    try:
        return await notifier.maybe_send(
            NotificationRequest(
                event=event,
                recipient=RecipientKind.CLIENT,
                chat_id=int(client.telegram_id),
                context=ReminderContext(
                    master_name=str(master.name),
                    slot_str=slot_str,
                ),
                facts=NotificationFacts(
                    event=event,
                    recipient=RecipientKind.CLIENT,
                    chat_id=int(client.telegram_id),
                    plan_is_pro=bool(plan_is_pro),
                    master_notify_clients=bool(getattr(master, "notify_clients", True)),
                    client_notifications_enabled=bool(getattr(client, "notifications_enabled", True)),
                    booking_start_at_utc=start_at_utc,
                    now_utc=now_utc,
                ),
            ),
        )
    except Exception:
        return None


def _client_booking_keyboard(
    *,
    event: NotificationEvent,
    booking: BookingEntity,
    now_utc: datetime,
) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []

    if event in {NotificationEvent.BOOKING_CREATED_CONFIRMED, NotificationEvent.BOOKING_RESCHEDULED_BY_MASTER}:
        rows.append(
            [
                InlineKeyboardButton(
                    text="💬 Написать мастеру",
                    url=f"tg://user?id={int(booking.master.telegram_id)}",
                ),
            ],
        )

    if event in {
        NotificationEvent.BOOKING_CREATED_CONFIRMED,
        NotificationEvent.BOOKING_RESCHEDULED_BY_MASTER,
        NotificationEvent.BOOKING_CONFIRMED,
    } and booking.start_at.astimezone(UTC) > now_utc:
        rows.append(
            [
                InlineKeyboardButton(
                    text=btn_cancel_booking(),
                    callback_data=f"c:bookings:cancel_ntf:{int(booking.id)}",
                ),
            ],
        )

    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_client_booking_notification(
    *,
    notifier: Notifier,
    event: NotificationEvent,
    booking: BookingEntity,
    chat_id: int,
    now_utc: datetime,
    plan_is_pro: bool,
) -> bool | None:
    if (
        booking.client.telegram_id is None
        or booking.client.notifications_enabled is False
        or booking.master.notify_clients is False
    ):
        return False

    if event in {NotificationEvent.BOOKING_CONFIRMED, NotificationEvent.BOOKING_DECLINED}:
        expected = (
            BookingStatus.CONFIRMED if event == NotificationEvent.BOOKING_CONFIRMED else BookingStatus.DECLINED
        )
        if booking.status != expected:
            return False

    start_at_utc = booking.start_at.astimezone(UTC)
    slot_client = to_zone(start_at_utc, booking.client.timezone)
    slot_str = slot_client.strftime("%d.%m.%Y %H:%M")

    try:
        return await notifier.maybe_send(
            NotificationRequest(
                event=event,
                recipient=RecipientKind.CLIENT,
                chat_id=int(chat_id),
                context=BookingContext(
                    booking_id=int(booking.id),
                    master_name=str(booking.master.name),
                    client_name=str(booking.client.name),
                    slot_str=slot_str,
                    duration_min=int(booking.duration_min),
                ),
                reply_markup=_client_booking_keyboard(event=event, booking=booking, now_utc=now_utc),
                facts=NotificationFacts(
                    event=event,
                    recipient=RecipientKind.CLIENT,
                    chat_id=int(chat_id),
                    plan_is_pro=bool(plan_is_pro),
                    master_notify_clients=bool(booking.master.notify_clients),
                    client_notifications_enabled=bool(booking.client.notifications_enabled),
                    booking_start_at_utc=start_at_utc,
                    now_utc=now_utc,
                ),
            ),
        )
    except Exception:
        return None


async def _send_master_attendance_nudge(
    *,
    notifier: Notifier,
    chat_id: int,
    booking: BookingEntity,
    now_utc: datetime,
    plan_is_pro: bool,
) -> bool | None:
    if booking.status != BookingStatus.CONFIRMED:
        return False
    if booking.attendance_outcome != AttendanceOutcome.UNKNOWN:
        return False

    master = booking.master
    client = booking.client

    if not getattr(master, "notify_attendance", True):
        return False

    end_at_utc = booking.start_at.astimezone(UTC) + timedelta(minutes=int(booking.duration_min))
    if end_at_utc > now_utc:
        return False

    slot_master = to_zone(booking.start_at.astimezone(UTC), master.timezone)
    slot_str = slot_master.strftime("%d.%m.%Y %H:%M")
    try:
        return await notifier.maybe_send(
            NotificationRequest(
                event=NotificationEvent.MASTER_ATTENDANCE_NUDGE,
                recipient=RecipientKind.MASTER,
                chat_id=int(chat_id),
                context=BookingContext(
                    booking_id=int(booking.id),
                    master_name=str(master.name),
                    client_name=str(getattr(client, "name", "") or ""),
                    slot_str=slot_str,
                    duration_min=int(booking.duration_min),
                ),
                reply_markup=_attendance_keyboard(booking_id=int(booking.id)),
                facts=NotificationFacts(
                    event=NotificationEvent.MASTER_ATTENDANCE_NUDGE,
                    recipient=RecipientKind.MASTER,
                    chat_id=int(chat_id),
                    plan_is_pro=bool(plan_is_pro),
                    master_notify_attendance=bool(getattr(master, "notify_attendance", True)),
                ),
            ),
        )
    except Exception:
        return None


async def _send_master_onboarding_nudge(
    *,
    notifier: Notifier,
    event: NotificationEvent,
    job: ScheduledNotificationJob,
    chat_id: int,
    plan_cache: dict[int, bool],
) -> bool | None:
    if job.master_id is None:
        return False
    master = await _load_master_for_notification(master_id=int(job.master_id))
    if master is None:
        return False

    plan_is_pro = await _plan_is_pro(master_id=int(master.id), cache=plan_cache)
    try:
        return await notifier.maybe_send(
            NotificationRequest(
                event=event,
                recipient=RecipientKind.MASTER,
                chat_id=int(chat_id),
                context=OnboardingContext(master_name=str(master.name)),
                reply_markup=_onboarding_keyboard(event=event, sequence=job.sequence),
                facts=NotificationFacts(
                    event=event,
                    recipient=RecipientKind.MASTER,
                    chat_id=int(chat_id),
                    plan_is_pro=bool(plan_is_pro),
                    master_onboarding_nudges_enabled=bool(getattr(master, "onboarding_nudges_enabled", True)),
                ),
            ),
        )
    except Exception:
        return None


@dataclass(frozen=True)
class _BookingJobDispatch:
    event: NotificationEvent
    recipient: RecipientKind
    chat_id: int


async def _send_booking_job(
    *,
    notifier: Notifier,
    booking: BookingEntity,
    dispatch: _BookingJobDispatch,
    now_utc: datetime,
    plan_cache: dict[int, bool],
) -> bool | None:
    plan_is_pro = await _plan_is_pro(master_id=int(booking.master_id), cache=plan_cache)

    if dispatch.recipient == RecipientKind.CLIENT:
        result: bool | None = False
        if dispatch.event in _CLIENT_BOOKING_EVENTS:
            result = await _send_client_booking_notification(
                notifier=notifier,
                event=dispatch.event,
                booking=booking,
                chat_id=int(dispatch.chat_id),
                now_utc=now_utc,
                plan_is_pro=bool(plan_is_pro),
            )
        elif dispatch.event in _CLIENT_REMINDER_EVENTS:
            result = await _send_client_reminder(
                notifier=notifier,
                event=dispatch.event,
                booking=booking,
                now_utc=now_utc,
                plan_is_pro=bool(plan_is_pro),
            )
        return result

    if dispatch.recipient == RecipientKind.MASTER and dispatch.event == NotificationEvent.MASTER_ATTENDANCE_NUDGE:
        return await _send_master_attendance_nudge(
            notifier=notifier,
            chat_id=int(dispatch.chat_id),
            booking=booking,
            now_utc=now_utc,
            plan_is_pro=bool(plan_is_pro),
        )

    return False


async def _send_job(
    *,
    notifier: Notifier,
    event: NotificationEvent,
    recipient: RecipientKind,
    job: ScheduledNotificationJob,
    now_utc: datetime,
    plan_cache: dict[int, bool],
) -> bool | None:
    """
    Returns:
    - True: sent
    - False: skipped/denied (should be cancelled)
    - None: temporary error (should be retried)
    """
    booking_id = job.booking_id
    booking_start_at = job.booking_start_at
    chat_id = int(job.chat_id)

    if (
        event
        in {
        NotificationEvent.MASTER_ONBOARDING_ADD_FIRST_CLIENT,
        NotificationEvent.MASTER_ONBOARDING_ADD_FIRST_BOOKING,
        }
        and recipient == RecipientKind.MASTER
    ):
        return await _send_master_onboarding_nudge(
            notifier=notifier,
            event=event,
            job=job,
            chat_id=int(chat_id),
            plan_cache=plan_cache,
        )

    if booking_id is None:
        if recipient == RecipientKind.MASTER and event in (_TRIAL_SUBSCRIPTION_EVENTS | _PRO_SUBSCRIPTION_EVENTS):
            return await _send_subscription_job(
                notifier=notifier,
                event=event,
                job=job,
                chat_id=chat_id,
                now_utc=now_utc,
                plan_cache=plan_cache,
            )
        return False

    booking = await _load_booking_for_notification(booking_id=booking_id)
    if booking is None or (booking_start_at is not None and booking.start_at != booking_start_at):
        return False
    return await _send_booking_job(
        notifier=notifier,
        booking=booking,
        dispatch=_BookingJobDispatch(event=event, recipient=recipient, chat_id=int(chat_id)),
        now_utc=now_utc,
        plan_cache=plan_cache,
    )


@dataclass(frozen=True)
class RemindersLoopConfig:
    tick: timedelta
    schedule_refresh: timedelta
    schedule_lookahead: timedelta
    attendance_lookback: timedelta
    batch_size: int
    retry_backoff: timedelta
    max_attempts: int


async def _maybe_refresh_schedule(
    *,
    now_utc: datetime,
    last_refresh_at: datetime | None,
    config: RemindersLoopConfig,
) -> datetime:
    if last_refresh_at is not None and (now_utc - last_refresh_at) < config.schedule_refresh:
        return last_refresh_at
    try:
        async with active_session() as session:
            repo = ScheduledNotificationRepository(session)
            inserted_reminders = await repo.schedule_client_booking_reminders(
                now_utc=now_utc,
                lookahead=config.schedule_lookahead,
            )
            inserted_att = await repo.schedule_master_attendance_nudges(
                now_utc=now_utc,
                lookback=config.attendance_lookback,
            )
            inserted_subs = await repo.schedule_subscription_expiry_reminders(
                now_utc=now_utc,
                lookahead=config.schedule_lookahead,
            )
        ev.info(
            "reminders.schedule_refresh",
            inserted_reminders=int(inserted_reminders),
            inserted_attendance=int(inserted_att),
            inserted_subscription=int(inserted_subs),
            lookahead_days=int(config.schedule_lookahead.total_seconds() // 86400),
            lookback_days=int(config.attendance_lookback.total_seconds() // 86400),
        )
    except Exception as exc:
        await ev.aexception("reminders.schedule_refresh_failed", exc=exc)
    return now_utc


async def _reserve_jobs(*, now_utc: datetime, batch_size: int) -> list:
    async with active_session() as session:
        repo = ScheduledNotificationRepository(session)
        return await repo.reserve_due(now_utc=now_utc, limit=int(batch_size))


async def _finalize_job(
    *,
    job_id: int,
    result: bool | None,
    now_utc: datetime,
    config: RemindersLoopConfig,
) -> bool:
    async with active_session() as session:
        repo = ScheduledNotificationRepository(session)
        if result is True:
            await repo.mark_sent(notification_id=int(job_id), now_utc=now_utc)
            return True
        if result is None:
            await repo.reschedule_after_error(
                notification_id=int(job_id),
                now_utc=now_utc,
                error="send_error",
                backoff=config.retry_backoff,
                max_attempts=int(config.max_attempts),
            )
            return False
        await repo.cancel(notification_id=int(job_id), reason="skipped")
        return False


async def run_loop(*, redis: Redis, notifier: Notifier, config: RemindersLoopConfig) -> None:
    last_heartbeat_log_at: datetime | None = None
    last_schedule_refresh_at: datetime | None = None

    while True:
        now_utc = datetime.now(UTC)
        obs = get_settings().observability
        await write_worker_heartbeat(
            redis,
            worker="reminders",
            ttl=timedelta(seconds=int(obs.workers_heartbeat_ttl_sec)),
            now_utc=now_utc,
            ev=ev,
        )
        if (
            last_heartbeat_log_at is None
            or (now_utc - last_heartbeat_log_at).total_seconds() >= float(obs.workers_heartbeat_log_every_sec)
        ):
            ev.info(
                "workers.reminders.heartbeat",
                ttl_sec=int(obs.workers_heartbeat_ttl_sec),
                log_every_sec=int(obs.workers_heartbeat_log_every_sec),
            )
            last_heartbeat_log_at = now_utc

        sent = 0
        reserved = 0
        plan_cache: dict[int, bool] = {}
        last_schedule_refresh_at = await _maybe_refresh_schedule(
            now_utc=now_utc,
            last_refresh_at=last_schedule_refresh_at,
            config=config,
        )

        try:
            jobs = await _reserve_jobs(now_utc=now_utc, batch_size=int(config.batch_size))
            reserved = len(jobs)
        except Exception as exc:
            await ev.aexception("reminders.reserve_failed", exc=exc)
            jobs = []

        for job in jobs:
            try:
                event = NotificationEvent(job.event)
                recipient = RecipientKind(job.recipient)
            except Exception:
                async with active_session() as session:
                    await ScheduledNotificationRepository(session).cancel(
                        notification_id=int(job.id),
                        reason="unknown_event_or_recipient",
                    )
                continue

            result = await _send_job(
                notifier=notifier,
                event=event,
                recipient=recipient,
                job=job,
                now_utc=now_utc,
                plan_cache=plan_cache,
            )
            try:
                sent += int(await _finalize_job(job_id=int(job.id), result=result, now_utc=now_utc, config=config))
            except Exception as exc:
                await ev.aexception("reminders.update_job_failed", exc=exc, job_id=int(job.id))

        ev.info(
            "reminders.tick",
            sent=sent,
            reserved=reserved,
            tick_sec=int(config.tick.total_seconds()),
        )
        await asyncio.sleep(float(config.tick.total_seconds()))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BeautyDesk reminder worker (Pro-only client reminders).")
    parser.add_argument("--env-file", default=None, help="Env file path (default: ENV_FILE or .env.local)")
    parser.add_argument("--tick-sec", type=int, default=int(os.getenv("REMINDERS_TICK_SEC", "30")))
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("REMINDERS_BATCH_SIZE", "50")))
    parser.add_argument(
        "--schedule-refresh-sec",
        type=int,
        default=int(os.getenv("REMINDERS_SCHEDULE_REFRESH_SEC", "600")),
    )
    parser.add_argument(
        "--schedule-lookahead-days",
        type=int,
        default=int(os.getenv("REMINDERS_SCHEDULE_LOOKAHEAD_DAYS", "30")),
    )
    parser.add_argument(
        "--attendance-lookback-days",
        type=int,
        default=int(os.getenv("ATTENDANCE_NUDGES_LOOKBACK_DAYS", "7")),
    )
    parser.add_argument("--retry-backoff-sec", type=int, default=int(os.getenv("REMINDERS_RETRY_BACKOFF_SEC", "60")))
    parser.add_argument("--max-attempts", type=int, default=int(os.getenv("REMINDERS_MAX_ATTEMPTS", "5")))
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
    start_metrics_server()

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
            config = RemindersLoopConfig(
                tick=timedelta(seconds=int(args.tick_sec)),
                schedule_refresh=timedelta(seconds=int(args.schedule_refresh_sec)),
                schedule_lookahead=timedelta(days=int(args.schedule_lookahead_days)),
                attendance_lookback=timedelta(days=int(args.attendance_lookback_days)),
                batch_size=int(args.batch_size),
                retry_backoff=timedelta(seconds=int(args.retry_backoff_sec)),
                max_attempts=int(args.max_attempts),
            )
            await run_loop(
                redis=redis,
                notifier=notifier,
                config=config,
            )
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
