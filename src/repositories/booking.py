from calendar import monthrange
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from src.models import Booking as BookingEntity
from src.repositories.base import BaseRepository
from src.schemas import BookingForReview
from src.schemas.booking import Booking, BookingCreate
from src.schemas.enums import BookingStatus


class BookingNotFound(Exception): ...


class BookingForbidden(Exception): ...


class BookingAlreadyHandled(Exception): ...


class BookingRepository(BaseRepository):
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

    async def get_for_master_in_range(
        self,
        *,
        master_id: int,
        start_at_utc: datetime,
        end_at_utc: datetime,
        statuses: set[BookingStatus] | None = None,
    ) -> list[Booking]:
        stmt = select(BookingEntity).where(
            BookingEntity.master_id == master_id,
            BookingEntity.start_at >= start_at_utc,
            BookingEntity.start_at < end_at_utc,
        )

        if statuses:
            stmt = stmt.where(BookingEntity.status.in_(statuses))

        stmt = stmt.order_by(BookingEntity.start_at.asc())

        result = await self._session.execute(stmt)
        return [Booking.model_validate(entity) for entity in result.scalars().all()]

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
    ) -> list[Booking]:
        start, end = self._day_bounds_utc(day)

        return await self.get_for_master_in_range(
            master_id=master_id,
            start_at_utc=start,
            end_at_utc=end,
            statuses=statuses,
        )

    async def get_for_master_on_week(
        self,
        *,
        master_id: int,
        week_start: date,
        statuses: set[BookingStatus] | None = None,
    ) -> list[Booking]:
        start, end = self._day_bounds_utc(week_start, delta=7)
        return await self.get_for_master_in_range(
            master_id=master_id,
            start_at_utc=start,
            end_at_utc=end,
            statuses=statuses,
        )

    async def get_for_master_on_month(
        self,
        *,
        master_id: int,
        year: int,
        month: int,
        statuses: set[BookingStatus] | None = None,
    ) -> list[Booking]:
        start = datetime(year, month, 1, tzinfo=UTC)
        days_in_month = monthrange(year, month)[1]
        end = start + timedelta(days=days_in_month)

        return await self.get_for_master_in_range(
            master_id=master_id,
            start_at_utc=start,
            end_at_utc=end,
            statuses=statuses,
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
        stmt = (
            update(BookingEntity)
            .where(BookingEntity.id == booking_id)
            .values(status=status)
        )
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0

    async def get_for_client(
        self,
        *,
        client_id: int,
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
