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
    booking_start_at: datetime | None
    sequence: int | None
    due_at: datetime


QUIET_FROM = time(22, 0)
QUIET_TO = time(9, 0)


def shift_out_of_quiet_hours(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("Expected tz-aware datetime.")
    local_time = dt.timetz()
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
            SubscriptionEntity.plan == SubscriptionPlan.PRO,
            SubscriptionEntity.trial_until > now_utc,
            SubscriptionEntity.paid_until > now_utc,
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
            SubscriptionEntity.plan == SubscriptionPlan.PRO,
            SubscriptionEntity.trial_until > now_utc,
            SubscriptionEntity.paid_until > now_utc,
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

    async def snooze_attendance_nudges_for_booking(self, *, booking_id: int, due_at: datetime) -> int:
        stmt = (
            update(ScheduledNotificationEntity)
            .where(
                ScheduledNotificationEntity.booking_id == int(booking_id),
                ScheduledNotificationEntity.event == "master_attendance_nudge",
                ScheduledNotificationEntity.status == ScheduledNotificationStatus.PENDING.value,
            )
            .values(due_at=due_at, updated_at=func.now())
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return int(result.rowcount or 0)
