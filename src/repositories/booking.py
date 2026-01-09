from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from typing import Literal, overload

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import selectinload

from src.models import Booking as BookingEntity
from src.repositories.base import BaseRepository
from src.schemas import BookingForReview
from src.schemas.booking import Booking, BookingCreate
from src.schemas.enums import AttendanceOutcome, BookingStatus


class BookingNotFound(Exception): ...


class BookingForbidden(Exception): ...


class BookingAlreadyHandled(Exception): ...


class BookingRepository(BaseRepository):
    async def exists_any_for_master(self, *, master_id: int) -> bool:
        stmt = (
            select(func.count())
            .select_from(BookingEntity)
            .where(BookingEntity.master_id == int(master_id))
        )
        count = await self._session.scalar(stmt)
        return int(count or 0) > 0

    async def get_by_id(self, booking_id: int) -> Booking:
        stmt = select(BookingEntity).where(BookingEntity.id == booking_id)
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise BookingNotFound(f"Booking id={booking_id} not found.")
        return Booking.from_db_entity(entity)

    async def create(self, booking: BookingCreate) -> Booking:
        entity = booking.to_db_entity()
        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)
        return Booking.from_db_entity(entity)

    @overload
    async def get_for_master_in_range(
        self,
        *,
        master_id: int,
        start_at_utc: datetime,
        end_at_utc: datetime,
        statuses: set[BookingStatus] | None = None,
        load_clients: Literal[True],
    ) -> list[BookingForReview]: ...

    @overload
    async def get_for_master_in_range(
        self,
        *,
        master_id: int,
        start_at_utc: datetime,
        end_at_utc: datetime,
        statuses: set[BookingStatus] | None = None,
        load_clients: Literal[False] = False,
    ) -> list[Booking]: ...

    async def get_for_master_in_range(
        self,
        *,
        master_id: int,
        start_at_utc: datetime,
        end_at_utc: datetime,
        statuses: set[BookingStatus] | None = None,
        load_clients: bool = False,
    ):
        stmt = select(BookingEntity).where(
            BookingEntity.master_id == master_id,
            BookingEntity.start_at >= start_at_utc,
            BookingEntity.start_at < end_at_utc,
        )

        if load_clients:
            stmt = stmt.options(
                selectinload(BookingEntity.client),
                selectinload(BookingEntity.master),
            )

        if statuses:
            stmt = stmt.where(BookingEntity.status.in_(statuses))

        stmt = stmt.order_by(BookingEntity.start_at.asc())

        result = await self._session.execute(stmt)
        entities = result.scalars().all()

        if load_clients:
            return [BookingForReview.model_validate(entity) for entity in entities]
        return [Booking.model_validate(entity) for entity in entities]

    @staticmethod
    def _day_bounds_utc(day: date, *, delta: int = 1) -> tuple[datetime, datetime]:
        start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        end = start + timedelta(days=delta)
        return start, end

    async def get_for_master_on_day(
        self,
        *,
        master_id: int,
        day: date,
        statuses: set[BookingStatus] | None = None,
    ) -> list[BookingForReview]:
        start, end = self._day_bounds_utc(day)

        return await self.get_for_master_in_range(
            master_id=master_id,
            start_at_utc=start,
            end_at_utc=end,
            statuses=statuses,
            load_clients=True,
        )

    async def get_for_master_on_week(
        self,
        *,
        master_id: int,
        week_start: date,
        statuses: set[BookingStatus] | None = None,
    ) -> list[BookingForReview]:
        start, end = self._day_bounds_utc(week_start, delta=7)
        return await self.get_for_master_in_range(
            master_id=master_id,
            start_at_utc=start,
            end_at_utc=end,
            statuses=statuses,
            load_clients=True,
        )

    async def get_for_master_on_month(
        self,
        *,
        master_id: int,
        year: int,
        month: int,
        statuses: set[BookingStatus] | None = None,
    ) -> list[BookingForReview]:
        start = datetime(year, month, 1, tzinfo=UTC)
        days_in_month = monthrange(year, month)[1]
        end = start + timedelta(days=days_in_month)

        return await self.get_for_master_in_range(
            master_id=master_id,
            start_at_utc=start,
            end_at_utc=end,
            statuses=statuses,
            load_clients=True,
        )

    async def get_for_review(self, booking_id: int) -> BookingForReview:
        stmt = (
            select(BookingEntity)
            .where(BookingEntity.id == booking_id)
            .options(
                selectinload(BookingEntity.master),
                selectinload(BookingEntity.client),
            )
        )
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise BookingNotFound(f"Booking id={booking_id} not found.")

        return BookingForReview.model_validate(entity)

    async def set_status(self, booking_id: int, status: BookingStatus) -> bool:
        stmt = update(BookingEntity).where(BookingEntity.id == booking_id).values(status=status)
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0

    async def set_status_if_pending_for_master(
        self,
        *,
        booking_id: int,
        master_id: int,
        status: BookingStatus,
    ) -> bool:
        stmt = (
            update(BookingEntity)
            .where(
                BookingEntity.id == booking_id,
                BookingEntity.master_id == master_id,
                BookingEntity.status == BookingStatus.PENDING,
            )
            .values(status=status)
        )
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0

    async def cancel_by_master(self, *, booking_id: int, master_id: int) -> bool:
        stmt = (
            update(BookingEntity)
            .where(
                BookingEntity.id == booking_id,
                BookingEntity.master_id == master_id,
                BookingEntity.status.in_(BookingStatus.active()),
            )
            .values(status=BookingStatus.CANCELLED)
        )
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0

    async def set_attendance_if_ended_and_confirmed(
        self,
        *,
        booking_id: int,
        master_id: int,
        outcome: AttendanceOutcome,
        now_utc: datetime,
    ) -> bool:
        """
        Mark attendance outcome for a booking.

        Rules:
        - Only booking owner (master_id) can mark.
        - Only CONFIRMED bookings.
        - Only after the session end time.
        - Outcome can be set only once (UNKNOWN -> outcome).
        """
        end_at_expr = BookingEntity.start_at + (BookingEntity.duration_min * text("INTERVAL '1 minute'"))
        stmt = (
            update(BookingEntity)
            .where(
                BookingEntity.id == booking_id,
                BookingEntity.master_id == master_id,
                BookingEntity.status == BookingStatus.CONFIRMED,
                BookingEntity.attendance_outcome == AttendanceOutcome.UNKNOWN,
                end_at_expr <= now_utc,
            )
            .values(attendance_outcome=outcome)
        )
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0

    async def get_for_client(
        self,
        *,
        client_id: int,
        now_utc: datetime | None = None,
        statuses: set[BookingStatus] | None = None,
        limit: int = 30,
    ) -> list[BookingForReview]:
        stmt = (
            select(BookingEntity)
            .where(BookingEntity.client_id == client_id)
            .options(
                selectinload(BookingEntity.master),
                selectinload(BookingEntity.client),
            )
            .order_by(BookingEntity.start_at.asc())
            .limit(limit)
        )
        if statuses:
            stmt = stmt.where(BookingEntity.status.in_(statuses))
        if now_utc is not None:
            end_at_expr = BookingEntity.start_at + (BookingEntity.duration_min * text("INTERVAL '1 minute'"))
            stmt = stmt.where(end_at_expr > now_utc)

        result = await self._session.execute(stmt)

        return [BookingForReview.model_validate(entity) for entity in result.scalars().all()]

    async def cancel_by_client(
        self,
        *,
        client_id: int,
        booking_id: int,
    ) -> bool:
        now_utc = datetime.now(UTC)
        stmt = (
            update(BookingEntity)
            .where(
                BookingEntity.id == booking_id,
                BookingEntity.client_id == client_id,
                BookingEntity.start_at > now_utc,
                BookingEntity.status.in_(BookingStatus.active()),
            )
            .values(status=BookingStatus.CANCELLED)
        )
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0

    async def reassign_client_for_master(
        self,
        *,
        master_id: int,
        from_client_id: int,
        to_client_id: int,
    ) -> int:
        stmt = (
            update(BookingEntity)
            .where(
                BookingEntity.master_id == master_id,
                BookingEntity.client_id == from_client_id,
            )
            .values(client_id=to_client_id)
        )
        result = await self._session.execute(stmt)
        return int(result.rowcount or 0)

    async def count_created_for_master_in_range(
        self,
        *,
        master_id: int,
        start_at_utc: datetime,
        end_at_utc: datetime,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(BookingEntity)
            .where(
                BookingEntity.master_id == master_id,
                BookingEntity.created_at >= start_at_utc,
                BookingEntity.created_at < end_at_utc,
            )
        )
        count = await self._session.scalar(stmt)
        return int(count or 0)

    async def count_by_start_at_for_master_in_range(
        self,
        *,
        master_id: int,
        start_at_utc: datetime,
        end_at_utc: datetime,
    ) -> int:
        """
        Counts bookings by visit time (Booking.start_at), not by creation time.

        Used for "bookings per month" entitlements and near-limit warnings.
        """
        stmt = (
            select(func.count())
            .select_from(BookingEntity)
            .where(
                BookingEntity.master_id == master_id,
                BookingEntity.start_at >= start_at_utc,
                BookingEntity.start_at < end_at_utc,
            )
        )
        count = await self._session.scalar(stmt)
        return int(count or 0)

    async def reschedule(
        self,
        *,
        booking_id: int,
        master_id: int,
        start_at: datetime,
    ) -> bool:
        """
        Updates booking start time.

        May raise IntegrityError if the new time overlaps an existing active booking
        due to the DB exclusion constraint.
        """
        stmt = (
            update(BookingEntity)
            .where(
                BookingEntity.id == booking_id,
                BookingEntity.master_id == master_id,
            )
            .values(start_at=start_at)
        )
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0
