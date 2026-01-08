from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum

from sqlalchemy import func, literal, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.datetime_utils import get_timezone
from src.models import (
    Booking as BookingEntity,
    Client as ClientEntity,
    Master as MasterEntity,
    ScheduledNotification as ScheduledNotificationEntity,
    Subscription as SubscriptionEntity,
    SubscriptionPlan,
)
from src.repositories.base import BaseRepository
from src.schemas.enums import AttendanceOutcome, BookingStatus


class ScheduledNotificationStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class ScheduledNotificationJob:
    id: int
    event: str
    recipient: str
    chat_id: int
    booking_id: int | None
    master_id: int | None
    client_id: int | None
    invoice_id: int | None
    booking_start_at: datetime | None
    sequence: int | None
    due_at: datetime


@dataclass(frozen=True)
class SnoozeAttendanceNudgeRequest:
    booking_id: int
    master_id: int
    master_telegram_id: int
    client_id: int
    booking_start_at: datetime
    due_at: datetime
    now_utc: datetime


QUIET_FROM = time(22, 0)
QUIET_TO = time(9, 0)


def shift_out_of_quiet_hours(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("Expected tz-aware datetime.")
    # Compare using local wall-clock time (naive) to avoid tz-aware time comparison pitfalls.
    local_time = dt.timetz().replace(tzinfo=None)
    in_quiet = (local_time >= QUIET_FROM) or (local_time < QUIET_TO)
    if not in_quiet:
        return dt
    target = dt.replace(hour=9, minute=0, second=0, microsecond=0)
    if local_time >= QUIET_FROM:
        target += timedelta(days=1)
    return target


def booking_end_at_utc(*, start_at: datetime, duration_min: int) -> datetime:
    if start_at.tzinfo is None:
        raise ValueError("Expected tz-aware datetime.")
    return start_at.astimezone(UTC) + timedelta(minutes=int(duration_min))


class ScheduledNotificationRepository(BaseRepository):
    async def upsert_invoice_payment_reminder(
        self,
        *,
        master_id: int,
        master_telegram_id: int,
        invoice_id: int,
        due_at_utc: datetime,
        now_utc: datetime,
        dedup_key: str,
    ) -> int:
        if now_utc.tzinfo is None:
            raise ValueError("Expected tz-aware datetime in UTC.")
        if due_at_utc.tzinfo is None:
            raise ValueError("Expected tz-aware due_at_utc in UTC.")

        ins = pg_insert(ScheduledNotificationEntity).values(
            {
                "event": "pro_invoice_reminder",
                "recipient": "master",
                "chat_id": int(master_telegram_id),
                "master_id": int(master_id),
                "client_id": None,
                "booking_id": None,
                "invoice_id": int(invoice_id),
                "booking_start_at": None,
                "status": ScheduledNotificationStatus.PENDING.value,
                "due_at": due_at_utc,
                "sequence": None,
                "dedup_key": str(dedup_key),
                "locked_at": None,
                "attempts": 0,
                "last_error": None,
                "sent_at": None,
                "created_at": now_utc,
                "updated_at": now_utc,
            },
        )
        stmt = ins.on_conflict_do_update(
            index_elements=["dedup_key"],
            set_={
                "chat_id": ins.excluded.chat_id,
                "master_id": ins.excluded.master_id,
                "invoice_id": ins.excluded.invoice_id,
                "status": ScheduledNotificationStatus.PENDING.value,
                "due_at": ins.excluded.due_at,
                "locked_at": None,
                "attempts": 0,
                "last_error": None,
                "sent_at": None,
                "updated_at": now_utc,
            },
            where=ScheduledNotificationEntity.status != ScheduledNotificationStatus.SENT.value,
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return int(result.rowcount or 0)

    async def schedule_pro_invoice_payment_reminder(
        self,
        *,
        master_id: int,
        master_telegram_id: int,
        master_timezone: str,
        invoice_id: int,
        now_utc: datetime,
    ) -> int:
        if now_utc.tzinfo is None:
            raise ValueError("Expected tz-aware datetime in UTC.")
        master_tz = get_timezone(str(master_timezone))
        local_now = now_utc.astimezone(master_tz)
        day = local_now.date() + timedelta(days=1)
        local_due = shift_out_of_quiet_hours(datetime.combine(day, time(11, 0), tzinfo=master_tz))
        due_at_utc = local_due.astimezone(UTC)

        dedup_key = f"beautydesk:outbox:billing:pro_invoice_reminder:{int(invoice_id)}"
        return await self.upsert_invoice_payment_reminder(
            master_id=master_id,
            master_telegram_id=master_telegram_id,
            invoice_id=invoice_id,
            due_at_utc=due_at_utc,
            now_utc=now_utc,
            dedup_key=dedup_key,
        )

    async def enqueue_booking_client_notification(
        self,
        *,
        event: str,
        chat_id: int,
        booking_id: int,
        booking_start_at: datetime,
        now_utc: datetime,
    ) -> int:
        """
        Enqueue an immediate client-facing booking notification to be delivered by the reminders worker.
        Idempotent via unique `dedup_key`.

        `booking_start_at` is persisted to invalidate stale jobs on reschedule (start_at mismatch).
        """
        if now_utc.tzinfo is None:
            raise ValueError("Expected tz-aware datetime in UTC.")
        if booking_start_at.tzinfo is None:
            raise ValueError("Expected tz-aware booking_start_at.")

        start_ts = int(booking_start_at.astimezone(UTC).timestamp())
        dedup_key = f"beautydesk:outbox:client_booking:{event}:{int(booking_id)}:{start_ts}"

        stmt = (
            pg_insert(ScheduledNotificationEntity)
            .values(
                {
                    "event": str(event),
                    "recipient": "client",
                    "chat_id": int(chat_id),
                    "master_id": None,
                    "client_id": None,
                    "booking_id": int(booking_id),
                    "booking_start_at": booking_start_at,
                    "status": ScheduledNotificationStatus.PENDING.value,
                    "due_at": now_utc,
                    "sequence": None,
                    "dedup_key": dedup_key,
                    "locked_at": None,
                    "attempts": 0,
                    "last_error": None,
                    "sent_at": None,
                    "created_at": now_utc,
                    "updated_at": now_utc,
                },
            )
            .on_conflict_do_nothing(index_elements=["dedup_key"])
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return int(result.rowcount or 0)

    async def schedule_master_onboarding_add_first_client(
        self,
        *,
        master_id: int,
        master_telegram_id: int,
        master_timezone: str,
        master_created_at: datetime,
        now_utc: datetime,
    ) -> int:
        """
        Schedule a small onboarding chain for a new master with 0 clients:
        - +~55m
        - D+1 11:00 local
        - D+3 11:00 local
        """
        if now_utc.tzinfo is None:
            raise ValueError("Expected tz-aware datetime in UTC.")
        if master_created_at.tzinfo is None:
            raise ValueError("Expected tz-aware master_created_at.")

        master_tz = get_timezone(str(master_timezone))
        created_local = master_created_at.astimezone(master_tz)

        due_local_1 = shift_out_of_quiet_hours(created_local + timedelta(minutes=55))
        due_local_2 = shift_out_of_quiet_hours(
            datetime.combine(created_local.date() + timedelta(days=1), time(11, 0), tzinfo=master_tz),
        )
        due_local_3 = shift_out_of_quiet_hours(
            datetime.combine(created_local.date() + timedelta(days=3), time(11, 0), tzinfo=master_tz),
        )

        jobs = [
            (1, due_local_1.astimezone(UTC)),
            (2, due_local_2.astimezone(UTC)),
            (3, due_local_3.astimezone(UTC)),
        ]

        to_insert = []
        for sequence, due_at in jobs:
            to_insert.append(
                {
                    "event": "master_onboarding_add_first_client",
                    "recipient": "master",
                    "chat_id": int(master_telegram_id),
                    "master_id": int(master_id),
                    "client_id": None,
                    "booking_id": None,
                    "booking_start_at": None,
                    "status": ScheduledNotificationStatus.PENDING.value,
                    "due_at": due_at,
                    "sequence": int(sequence),
                    "dedup_key": f"beautydesk:outbox:onb:first_client:{int(master_id)}:{int(sequence)}",
                    "locked_at": None,
                    "attempts": 0,
                    "last_error": None,
                    "sent_at": None,
                    "created_at": now_utc,
                    "updated_at": now_utc,
                },
            )

        stmt_ins = (
            pg_insert(ScheduledNotificationEntity)
            .values(to_insert)
            .on_conflict_do_nothing(index_elements=["dedup_key"])
        )
        result = await self._session.execute(stmt_ins)
        await self._session.flush()
        return int(result.rowcount or 0)

    async def schedule_master_onboarding_add_first_booking(
        self,
        *,
        master_id: int,
        master_telegram_id: int,
        master_timezone: str,
        now_utc: datetime,
    ) -> int:
        """
        Schedule a small onboarding chain after the first client was added:
        - +~55m
        - D+1 11:00 local
        - D+3 11:00 local
        """
        if now_utc.tzinfo is None:
            raise ValueError("Expected tz-aware datetime in UTC.")

        master_tz = get_timezone(str(master_timezone))
        local_now = now_utc.astimezone(master_tz)

        due_local_1 = shift_out_of_quiet_hours(local_now + timedelta(minutes=55))
        due_local_2 = shift_out_of_quiet_hours(
            datetime.combine(local_now.date() + timedelta(days=1), time(11, 0), tzinfo=master_tz),
        )
        due_local_3 = shift_out_of_quiet_hours(
            datetime.combine(local_now.date() + timedelta(days=3), time(11, 0), tzinfo=master_tz),
        )

        jobs = [
            (1, due_local_1.astimezone(UTC)),
            (2, due_local_2.astimezone(UTC)),
            (3, due_local_3.astimezone(UTC)),
        ]

        to_insert = []
        for sequence, due_at in jobs:
            to_insert.append(
                {
                    "event": "master_onboarding_add_first_booking",
                    "recipient": "master",
                    "chat_id": int(master_telegram_id),
                    "master_id": int(master_id),
                    "client_id": None,
                    "booking_id": None,
                    "booking_start_at": None,
                    "status": ScheduledNotificationStatus.PENDING.value,
                    "due_at": due_at,
                    "sequence": int(sequence),
                    "dedup_key": f"beautydesk:outbox:onb:first_booking:{int(master_id)}:{int(sequence)}",
                    "locked_at": None,
                    "attempts": 0,
                    "last_error": None,
                    "sent_at": None,
                    "created_at": now_utc,
                    "updated_at": now_utc,
                },
            )

        stmt_ins = (
            pg_insert(ScheduledNotificationEntity)
            .values(to_insert)
            .on_conflict_do_nothing(index_elements=["dedup_key"])
        )
        result = await self._session.execute(stmt_ins)
        await self._session.flush()
        return int(result.rowcount or 0)

    async def upsert_subscription_reminders(
        self,
        *,
        master_id: int,
        master_telegram_id: int,
        master_timezone: str,
        now_utc: datetime,
        items: list[tuple[str, datetime, int]],
        dedup_prefix: str,
    ) -> int:
        """
        Upsert subscription reminder jobs.

        `items`: list of (event, due_at_utc, sequence)
        """
        if now_utc.tzinfo is None:
            raise ValueError("Expected tz-aware datetime in UTC.")

        to_insert: list[dict[str, object]] = []
        for event, due_at, sequence in items:
            key = f"beautydesk:outbox:{dedup_prefix}:{int(master_id)}:{int(sequence)}"
            to_insert.append(
                {
                    "event": str(event),
                    "recipient": "master",
                    "chat_id": int(master_telegram_id),
                    "master_id": int(master_id),
                    "client_id": None,
                    "booking_id": None,
                    "booking_start_at": None,
                    "status": ScheduledNotificationStatus.PENDING.value,
                    "due_at": due_at,
                    "sequence": int(sequence),
                    "dedup_key": key,
                    "locked_at": None,
                    "attempts": 0,
                    "last_error": None,
                    "sent_at": None,
                    "created_at": now_utc,
                    "updated_at": now_utc,
                },
            )

        ins = pg_insert(ScheduledNotificationEntity).values(to_insert)
        stmt = ins.on_conflict_do_update(
            index_elements=["dedup_key"],
            set_={
                "event": ins.excluded.event,
                "recipient": ins.excluded.recipient,
                "chat_id": ins.excluded.chat_id,
                "master_id": ins.excluded.master_id,
                "status": ScheduledNotificationStatus.PENDING.value,
                "due_at": ins.excluded.due_at,
                "sequence": ins.excluded.sequence,
                "locked_at": None,
                "attempts": 0,
                "last_error": None,
                "sent_at": None,
                "updated_at": now_utc,
            },
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return int(result.rowcount or 0)

    async def schedule_trial_expiry_reminders(
        self,
        *,
        master_id: int,
        master_telegram_id: int,
        master_timezone: str,
        trial_until_utc: datetime,
        now_utc: datetime,
    ) -> int:
        if trial_until_utc.tzinfo is None:
            raise ValueError("Expected tz-aware trial_until_utc.")
        master_tz = get_timezone(str(master_timezone))
        expiry_day = trial_until_utc.astimezone(master_tz).date()

        def at_morning_utc(day):
            local = shift_out_of_quiet_hours(datetime.combine(day, time(11, 0), tzinfo=master_tz))
            return local.astimezone(UTC)

        items = [
            ("trial_expiring_d3", at_morning_utc(expiry_day - timedelta(days=3)), 1),
            ("trial_expiring_d1", at_morning_utc(expiry_day - timedelta(days=1)), 2),
            ("trial_expiring_d0", at_morning_utc(expiry_day), 3),
        ]
        return await self.upsert_subscription_reminders(
            master_id=master_id,
            master_telegram_id=master_telegram_id,
            master_timezone=master_timezone,
            now_utc=now_utc,
            items=items,
            dedup_prefix="sub:trial",
        )

    async def schedule_pro_expiry_reminders(
        self,
        *,
        master_id: int,
        master_telegram_id: int,
        master_timezone: str,
        paid_until_utc: datetime,
        now_utc: datetime,
    ) -> int:
        if paid_until_utc.tzinfo is None:
            raise ValueError("Expected tz-aware paid_until_utc.")
        master_tz = get_timezone(str(master_timezone))
        expiry_day = paid_until_utc.astimezone(master_tz).date()

        def at_morning_utc(day):
            local = shift_out_of_quiet_hours(datetime.combine(day, time(11, 0), tzinfo=master_tz))
            return local.astimezone(UTC)

        items = [
            ("pro_expiring_d5", at_morning_utc(expiry_day - timedelta(days=5)), 1),
            ("pro_expiring_d2", at_morning_utc(expiry_day - timedelta(days=2)), 2),
            ("pro_expiring_d0", at_morning_utc(expiry_day), 3),
            ("pro_expired_recovery_d1", at_morning_utc(expiry_day + timedelta(days=1)), 4),
        ]
        return await self.upsert_subscription_reminders(
            master_id=master_id,
            master_telegram_id=master_telegram_id,
            master_timezone=master_timezone,
            now_utc=now_utc,
            items=items,
            dedup_prefix="sub:pro",
        )

    async def schedule_subscription_expiry_reminders(self, *, now_utc: datetime, lookahead: timedelta) -> int:
        """
        Best-effort scheduler for subscription expiry reminders.

        This is meant to keep reminders in sync even if the bot was down at the time
        of trial/pro start (or after deploying new reminder rules).
        """
        if now_utc.tzinfo is None:
            raise ValueError("Expected tz-aware datetime in UTC.")

        inserted = 0
        horizon_trial = now_utc + lookahead + timedelta(days=3)
        horizon_pro = now_utc + lookahead + timedelta(days=5)
        pro_recent_cutoff = now_utc - timedelta(days=2)

        trial_stmt = (
            select(MasterEntity.id, MasterEntity.telegram_id, MasterEntity.timezone, SubscriptionEntity.trial_until)
            .join(SubscriptionEntity, SubscriptionEntity.master_id == MasterEntity.id)
            .where(
                SubscriptionEntity.trial_until.is_not(None),
                SubscriptionEntity.trial_until > now_utc,
                SubscriptionEntity.trial_until <= horizon_trial,
                # If a paid period is active, trial-expiry reminders are irrelevant.
                or_(SubscriptionEntity.paid_until.is_(None), SubscriptionEntity.paid_until <= now_utc),
            )
        )
        trial_rows = (await self._session.execute(trial_stmt)).all()
        for master_id, telegram_id, tz, trial_until in trial_rows:
            if trial_until is None:
                continue
            inserted += await self.schedule_trial_expiry_reminders(
                master_id=int(master_id),
                master_telegram_id=int(telegram_id),
                master_timezone=str(tz.value),
                trial_until_utc=trial_until,
                now_utc=now_utc,
            )

        pro_stmt = (
            select(MasterEntity.id, MasterEntity.telegram_id, MasterEntity.timezone, SubscriptionEntity.paid_until)
            .join(SubscriptionEntity, SubscriptionEntity.master_id == MasterEntity.id)
            .where(
                SubscriptionEntity.paid_until.is_not(None),
                SubscriptionEntity.paid_until >= pro_recent_cutoff,
                SubscriptionEntity.paid_until <= horizon_pro,
            )
        )
        pro_rows = (await self._session.execute(pro_stmt)).all()
        for master_id, telegram_id, tz, paid_until in pro_rows:
            if paid_until is None:
                continue
            inserted += await self.schedule_pro_expiry_reminders(
                master_id=int(master_id),
                master_telegram_id=int(telegram_id),
                master_timezone=str(tz.value),
                paid_until_utc=paid_until,
                now_utc=now_utc,
            )

        return inserted

    async def schedule_client_booking_reminders(
        self,
        *,
        now_utc: datetime,
        lookahead: timedelta,
    ) -> int:
        """
        Insert PENDING jobs for upcoming bookings (2h/24h reminders).
        Idempotent via unique `dedup_key`.
        """
        if now_utc.tzinfo is None:
            raise ValueError("Expected tz-aware datetime in UTC.")

        end_at = now_utc + lookahead
        pro_active = or_(
            # active paid access
            SubscriptionEntity.paid_until > now_utc,
            # active trial access
            SubscriptionEntity.trial_until > now_utc,
            # lifetime Pro (plan==PRO and no expiry timestamps)
            (
                (SubscriptionEntity.plan == SubscriptionPlan.PRO)
                & (SubscriptionEntity.paid_until.is_(None))
                & (SubscriptionEntity.trial_until.is_(None))
            ),
        )

        inserted = 0
        for name, interval_literal, event in (
            ("24h", "24 hours", "reminder_24h"),
            ("2h", "2 hours", "reminder_2h"),
        ):
            due_at_expr = BookingEntity.start_at - text(f"INTERVAL '{interval_literal}'")
            start_ts = func.floor(func.extract("epoch", BookingEntity.start_at))
            dedup_key_expr = func.concat(
                "beautydesk:outbox:reminder:",
                literal(name),
                ":",
                BookingEntity.id,
                ":",
                start_ts,
            )

            stmt = (
                pg_insert(ScheduledNotificationEntity)
                .from_select(
                    [
                        "event",
                        "recipient",
                        "chat_id",
                        "master_id",
                        "client_id",
                        "booking_id",
                        "booking_start_at",
                        "status",
                        "due_at",
                        "sequence",
                        "dedup_key",
                        "locked_at",
                        "attempts",
                        "last_error",
                        "sent_at",
                        "created_at",
                        "updated_at",
                    ],
                    select(
                        literal(event),
                        literal("client"),
                        ClientEntity.telegram_id,
                        BookingEntity.master_id,
                        BookingEntity.client_id,
                        BookingEntity.id,
                        BookingEntity.start_at,
                        literal(ScheduledNotificationStatus.PENDING.value),
                        due_at_expr,
                        literal(None),
                        dedup_key_expr,
                        literal(None),
                        literal(0),
                        literal(None),
                        literal(None),
                        func.now(),
                        func.now(),
                    )
                    .select_from(BookingEntity)
                    .join(ClientEntity, ClientEntity.id == BookingEntity.client_id)
                    .join(MasterEntity, MasterEntity.id == BookingEntity.master_id)
                    .join(SubscriptionEntity, SubscriptionEntity.master_id == MasterEntity.id)
                    .where(
                        BookingEntity.status == BookingStatus.CONFIRMED,
                        BookingEntity.start_at > now_utc,
                        BookingEntity.start_at < end_at,
                        ClientEntity.telegram_id.is_not(None),
                        ClientEntity.notifications_enabled.is_(True),
                        MasterEntity.notify_clients.is_(True),
                        pro_active,
                    ),
                )
                .on_conflict_do_nothing(index_elements=["dedup_key"])
            )
            result = await self._session.execute(stmt)
            inserted += int(result.rowcount or 0)
        await self._session.flush()
        return inserted

    async def schedule_master_attendance_nudges(
        self,
        *,
        now_utc: datetime,
        lookback: timedelta,
    ) -> int:
        """
        Schedule Pro-only attendance nudges for ended bookings with UNKNOWN outcome.
        Creates up to two jobs per booking: +36h and +72h after session end,
        shifted out of quiet hours in master's timezone.
        """
        if now_utc.tzinfo is None:
            raise ValueError("Expected tz-aware datetime in UTC.")

        pro_active = or_(
            SubscriptionEntity.trial_until > now_utc,
            SubscriptionEntity.paid_until > now_utc,
            (
                (SubscriptionEntity.plan == SubscriptionPlan.PRO)
                & (SubscriptionEntity.paid_until.is_(None))
                & (SubscriptionEntity.trial_until.is_(None))
            ),
        )
        end_at_expr = BookingEntity.start_at + (BookingEntity.duration_min * text("INTERVAL '1 minute'"))
        start_cutoff = now_utc - lookback

        stmt = (
            select(BookingEntity, MasterEntity, ClientEntity)
            .select_from(BookingEntity)
            .join(MasterEntity, MasterEntity.id == BookingEntity.master_id)
            .join(ClientEntity, ClientEntity.id == BookingEntity.client_id)
            .join(SubscriptionEntity, SubscriptionEntity.master_id == MasterEntity.id)
            .where(
                BookingEntity.status == BookingStatus.CONFIRMED,
                BookingEntity.attendance_outcome == AttendanceOutcome.UNKNOWN,
                MasterEntity.notify_attendance.is_(True),
                pro_active,
                end_at_expr <= now_utc,
                end_at_expr >= start_cutoff,
            )
        )
        rows = (await self._session.execute(stmt)).all()

        to_insert: list[dict] = []
        for booking, master, client in rows:
            end_at_utc = booking_end_at_utc(start_at=booking.start_at, duration_min=int(booking.duration_min))
            master_tz = get_timezone(str(master.timezone.value))

            for sequence, offset in ((1, timedelta(hours=36)), (2, timedelta(hours=72))):
                due_local = shift_out_of_quiet_hours((end_at_utc + offset).astimezone(master_tz))
                due_at = due_local.astimezone(UTC)
                start_ts = int(booking.start_at.astimezone(UTC).timestamp())
                dedup_key = f"beautydesk:outbox:attendance:{int(booking.id)}:{start_ts}:{sequence}"
                to_insert.append(
                    {
                        "event": "master_attendance_nudge",
                        "recipient": "master",
                        "chat_id": int(master.telegram_id),
                        "master_id": int(master.id),
                        "client_id": int(client.id),
                        "booking_id": int(booking.id),
                        "booking_start_at": booking.start_at,
                        "status": ScheduledNotificationStatus.PENDING.value,
                        "due_at": due_at,
                        "sequence": int(sequence),
                        "dedup_key": dedup_key,
                        "locked_at": None,
                        "attempts": 0,
                        "last_error": None,
                        "sent_at": None,
                        "created_at": now_utc,
                        "updated_at": now_utc,
                    },
                )

        if not to_insert:
            return 0

        stmt_ins = (
            pg_insert(ScheduledNotificationEntity)
            .values(to_insert)
            .on_conflict_do_nothing(index_elements=["dedup_key"])
        )
        result = await self._session.execute(stmt_ins)
        await self._session.flush()
        return int(result.rowcount or 0)

    async def reserve_due(
        self,
        *,
        now_utc: datetime,
        limit: int,
    ) -> list[ScheduledNotificationJob]:
        if now_utc.tzinfo is None:
            raise ValueError("Expected tz-aware datetime in UTC.")

        stmt = (
            select(ScheduledNotificationEntity)
            .where(
                ScheduledNotificationEntity.status == ScheduledNotificationStatus.PENDING.value,
                ScheduledNotificationEntity.due_at <= now_utc,
            )
            .order_by(ScheduledNotificationEntity.due_at.asc(), ScheduledNotificationEntity.id.asc())
            .limit(int(limit))
            .with_for_update(skip_locked=True)
        )
        entities = list((await self._session.scalars(stmt)).all())
        if not entities:
            return []

        ids = [int(e.id) for e in entities]
        await self._session.execute(
            update(ScheduledNotificationEntity)
            .where(ScheduledNotificationEntity.id.in_(ids))
            .values(
                status=ScheduledNotificationStatus.SENDING.value,
                locked_at=now_utc,
                attempts=ScheduledNotificationEntity.attempts + 1,
                updated_at=func.now(),
            ),
        )
        await self._session.flush()

        return [
            ScheduledNotificationJob(
                id=int(e.id),
                event=str(e.event),
                recipient=str(e.recipient),
                chat_id=int(e.chat_id),
                booking_id=int(e.booking_id) if e.booking_id is not None else None,
                master_id=int(e.master_id) if e.master_id is not None else None,
                client_id=int(e.client_id) if e.client_id is not None else None,
                invoice_id=int(e.invoice_id) if getattr(e, "invoice_id", None) is not None else None,
                booking_start_at=e.booking_start_at,
                sequence=int(e.sequence) if e.sequence is not None else None,
                due_at=e.due_at,
            )
            for e in entities
        ]

    async def mark_sent(self, *, notification_id: int, now_utc: datetime) -> None:
        await self._session.execute(
            update(ScheduledNotificationEntity)
            .where(ScheduledNotificationEntity.id == int(notification_id))
            .values(
                status=ScheduledNotificationStatus.SENT.value,
                locked_at=None,
                sent_at=now_utc,
                last_error=None,
                updated_at=func.now(),
            ),
        )
        await self._session.flush()

    async def cancel(self, *, notification_id: int, reason: str | None = None) -> None:
        await self._session.execute(
            update(ScheduledNotificationEntity)
            .where(ScheduledNotificationEntity.id == int(notification_id))
            .values(
                status=ScheduledNotificationStatus.CANCELLED.value,
                locked_at=None,
                last_error=reason,
                updated_at=func.now(),
            ),
        )
        await self._session.flush()

    async def reschedule_after_error(
        self,
        *,
        notification_id: int,
        now_utc: datetime,
        error: str,
        backoff: timedelta,
        max_attempts: int,
    ) -> None:
        """
        Best-effort retry with backoff; once max_attempts exceeded, mark as FAILED.
        """
        entity = await self._session.scalar(
            select(ScheduledNotificationEntity).where(ScheduledNotificationEntity.id == int(notification_id)),
        )
        if entity is None:
            return
        attempts = int(getattr(entity, "attempts", 0) or 0)
        if attempts >= int(max_attempts):
            await self._session.execute(
                update(ScheduledNotificationEntity)
                .where(ScheduledNotificationEntity.id == int(notification_id))
                .values(
                    status=ScheduledNotificationStatus.FAILED.value,
                    locked_at=None,
                    last_error=error,
                    updated_at=func.now(),
                ),
            )
            await self._session.flush()
            return

        await self._session.execute(
            update(ScheduledNotificationEntity)
            .where(ScheduledNotificationEntity.id == int(notification_id))
            .values(
                status=ScheduledNotificationStatus.PENDING.value,
                locked_at=None,
                due_at=now_utc + backoff,
                last_error=error,
                updated_at=func.now(),
            ),
        )
        await self._session.flush()

    async def cancel_attendance_nudges_for_booking(self, *, booking_id: int) -> int:
        stmt = (
            update(ScheduledNotificationEntity)
            .where(
                ScheduledNotificationEntity.booking_id == int(booking_id),
                ScheduledNotificationEntity.event == "master_attendance_nudge",
                ScheduledNotificationEntity.status.in_(
                    [
                        ScheduledNotificationStatus.PENDING.value,
                        ScheduledNotificationStatus.SENDING.value,
                    ],
                ),
            )
            .values(
                status=ScheduledNotificationStatus.CANCELLED.value,
                locked_at=None,
                updated_at=func.now(),
            )
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return int(result.rowcount or 0)

    async def snooze_attendance_nudges_for_booking(
        self,
        *,
        request: SnoozeAttendanceNudgeRequest,
    ) -> int:
        """
        Snooze master attendance reminder to a specific time.

        Historically this was implemented as UPDATE of existing pending nudges by booking_id.
        That fails when there are no pending rows left (e.g. the nudge already sent), and it can
        unintentionally shift multiple future nudges at once.

        Current behavior:
        - cancel any pending/sending attendance nudges for the booking
        - upsert a single "snoozed" attendance nudge with a stable dedup key
        """
        if request.due_at.tzinfo is None:
            raise ValueError("Expected tz-aware due_at.")
        if request.now_utc.tzinfo is None:
            raise ValueError("Expected tz-aware now_utc in UTC.")
        if request.booking_start_at.tzinfo is None:
            raise ValueError("Expected tz-aware booking_start_at.")

        await self.cancel_attendance_nudges_for_booking(booking_id=int(request.booking_id))

        dedup_key = f"beautydesk:outbox:attendance:snooze:{int(request.booking_id)}"
        ins = pg_insert(ScheduledNotificationEntity).values(
            {
                "event": "master_attendance_nudge",
                "recipient": "master",
                "chat_id": int(request.master_telegram_id),
                "master_id": int(request.master_id),
                "client_id": int(request.client_id),
                "booking_id": int(request.booking_id),
                "invoice_id": None,
                "booking_start_at": request.booking_start_at,
                "status": ScheduledNotificationStatus.PENDING.value,
                "due_at": request.due_at,
                "sequence": None,
                "dedup_key": dedup_key,
                "locked_at": None,
                "attempts": 0,
                "last_error": None,
                "sent_at": None,
                "created_at": request.now_utc,
                "updated_at": request.now_utc,
            },
        )
        stmt = ins.on_conflict_do_update(
            index_elements=["dedup_key"],
            set_={
                "chat_id": ins.excluded.chat_id,
                "master_id": ins.excluded.master_id,
                "client_id": ins.excluded.client_id,
                "booking_id": ins.excluded.booking_id,
                "booking_start_at": ins.excluded.booking_start_at,
                "status": ScheduledNotificationStatus.PENDING.value,
                "due_at": ins.excluded.due_at,
                "locked_at": None,
                "attempts": 0,
                "last_error": None,
                "sent_at": None,
                "updated_at": request.now_utc,
            },
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return int(result.rowcount or 0)

    async def cancel_onboarding_for_master(self, *, master_id: int) -> int:
        stmt = (
            update(ScheduledNotificationEntity)
            .where(
                ScheduledNotificationEntity.master_id == int(master_id),
                ScheduledNotificationEntity.event.in_(
                    [
                        "master_onboarding_add_first_client",
                        "master_onboarding_add_first_booking",
                    ],
                ),
                ScheduledNotificationEntity.status.in_(
                    [
                        ScheduledNotificationStatus.PENDING.value,
                        ScheduledNotificationStatus.SENDING.value,
                    ],
                ),
            )
            .values(
                status=ScheduledNotificationStatus.CANCELLED.value,
                locked_at=None,
                updated_at=func.now(),
            )
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return int(result.rowcount or 0)
