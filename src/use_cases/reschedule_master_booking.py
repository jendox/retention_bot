from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.observability.events import EventLogger
from src.repositories import MasterNotFound, MasterRepository
from src.repositories.booking import BookingNotFound, BookingRepository
from src.schemas import BookingForReview, Master
from src.schemas.enums import BookingStatus
from src.use_cases.entitlements import EntitlementsService

ev = EventLogger(__name__)


class RescheduleMasterBookingError(StrEnum):
    INVALID_REQUEST = "invalid_request"
    MASTER_NOT_FOUND = "master_not_found"
    BOOKING_NOT_FOUND = "booking_not_found"
    FORBIDDEN = "forbidden"
    NOT_RESCHEDULABLE = "not_reschedulable"
    PAST_BOOKING = "past_booking"
    SAME_SLOT = "same_slot"
    PRO_REQUIRED = "pro_required"
    SLOT_NOT_AVAILABLE = "slot_not_available"


@dataclass(frozen=True)
class RescheduleMasterBookingRequest:
    master_telegram_id: int
    booking_id: int
    new_start_at_utc: datetime


@dataclass(frozen=True)
class RescheduleMasterBookingResult:
    ok: bool

    booking: BookingForReview | None = None
    master: Master | None = None
    plan_is_pro: bool | None = None

    error: RescheduleMasterBookingError | None = None
    error_detail: str | None = None


class RescheduleMasterBooking:
    """
    Reschedule an existing booking initiated by the master.

    Properties:
    - Ensures the booking belongs to the calling master (by telegram_id).
    - Validates both original booking and the new slot are in the future.
    - Enforces that the booking is active (PENDING/CONFIRMED).
    - Pro-only feature (plan check is enforced here to prevent bypassing UI gating).
    - Uses the DB exclusion constraint to prevent overlaps; maps IntegrityError to SLOT_NOT_AVAILABLE.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._master_repo = MasterRepository(session)
        self._booking_repo = BookingRepository(session)
        self._entitlements = EntitlementsService(session)

    class _Abort(Exception):
        def __init__(self, result: RescheduleMasterBookingResult) -> None:
            super().__init__("aborted")
            self.result = result

    @staticmethod
    def _error(
        *,
        error: RescheduleMasterBookingError,
        error_detail: str | None = None,
        master: Master | None = None,
        booking: BookingForReview | None = None,
        plan_is_pro: bool | None = None,
    ) -> RescheduleMasterBookingResult:
        return RescheduleMasterBookingResult(
            ok=False,
            master=master,
            booking=booking,
            plan_is_pro=plan_is_pro,
            error=error,
            error_detail=error_detail,
        )

    def _unwrap(self, value):
        if isinstance(value, RescheduleMasterBookingResult):
            raise self._Abort(value)
        return value

    def _abort_if(self, maybe_error: RescheduleMasterBookingResult | None) -> None:
        if maybe_error is not None:
            raise self._Abort(maybe_error)

    def _validate_ids(self, request: RescheduleMasterBookingRequest) -> RescheduleMasterBookingResult | None:
        if request.master_telegram_id <= 0 or request.booking_id <= 0:
            return self._error(
                error=RescheduleMasterBookingError.INVALID_REQUEST,
                error_detail="master_telegram_id/booking_id must be positive",
            )
        return None

    @staticmethod
    def _normalize_start_at(value: datetime) -> datetime | RescheduleMasterBookingResult:
        if value.tzinfo is None:
            return RescheduleMasterBooking._error(
                error=RescheduleMasterBookingError.INVALID_REQUEST,
                error_detail="new_start_at_utc must be timezone-aware",
            )
        value = value.astimezone(UTC)
        if value <= datetime.now(UTC):
            return RescheduleMasterBooking._error(
                error=RescheduleMasterBookingError.INVALID_REQUEST,
                error_detail="new_start_at_utc is in the past",
            )
        return value

    async def _load_master(self, telegram_id: int) -> Master | RescheduleMasterBookingResult:
        try:
            return await self._master_repo.get_by_telegram_id(telegram_id)
        except MasterNotFound:
            return self._error(
                error=RescheduleMasterBookingError.MASTER_NOT_FOUND,
                error_detail=f"master_telegram_id={telegram_id}",
            )

    async def _ensure_pro(self, *, master: Master) -> bool | RescheduleMasterBookingResult:
        plan = await self._entitlements.get_plan(master_id=master.id)
        if not plan.is_pro:
            return self._error(
                error=RescheduleMasterBookingError.PRO_REQUIRED,
                master=master,
                plan_is_pro=False,
            )
        return True

    async def _load_booking(
        self,
        *,
        booking_id: int,
        master: Master,
    ) -> BookingForReview | RescheduleMasterBookingResult:
        try:
            return await self._booking_repo.get_for_review(booking_id)
        except BookingNotFound:
            return self._error(
                error=RescheduleMasterBookingError.BOOKING_NOT_FOUND,
                error_detail=f"booking_id={booking_id}",
                master=master,
                plan_is_pro=True,
            )

    def _ensure_owner(self, *, booking: BookingForReview, master: Master) -> RescheduleMasterBookingResult | None:
        if booking.master.id != master.id:
            return self._error(
                error=RescheduleMasterBookingError.FORBIDDEN,
                error_detail=f"booking.master_id={booking.master.id} != master.id={master.id}",
                master=master,
                booking=booking,
                plan_is_pro=True,
            )
        return None

    def _ensure_active(self, *, booking: BookingForReview, master: Master) -> RescheduleMasterBookingResult | None:
        if booking.status not in BookingStatus.active():
            return self._error(
                error=RescheduleMasterBookingError.NOT_RESCHEDULABLE,
                master=master,
                booking=booking,
                plan_is_pro=True,
            )
        return None

    def _ensure_future(self, *, booking: BookingForReview, master: Master) -> RescheduleMasterBookingResult | None:
        now_utc = datetime.now(UTC)
        if booking.start_at <= now_utc:
            return self._error(
                error=RescheduleMasterBookingError.PAST_BOOKING,
                master=master,
                booking=booking,
                plan_is_pro=True,
            )
        return None

    def _ensure_changed(
        self,
        *,
        booking: BookingForReview,
        master: Master,
        new_start_at_utc: datetime,
    ) -> RescheduleMasterBookingResult | None:
        if booking.start_at.astimezone(UTC) == new_start_at_utc.astimezone(UTC):
            return self._error(
                error=RescheduleMasterBookingError.SAME_SLOT,
                master=master,
                booking=booking,
                plan_is_pro=True,
            )
        return None

    async def _reschedule(
        self,
        *,
        booking_id: int,
        master_id: int,
        new_start_at_utc: datetime,
        master: Master,
        booking: BookingForReview,
    ) -> None | RescheduleMasterBookingResult:
        try:
            updated = await self._booking_repo.reschedule(
                booking_id=booking_id,
                master_id=master_id,
                start_at=new_start_at_utc,
            )
        except IntegrityError:
            return self._error(
                error=RescheduleMasterBookingError.SLOT_NOT_AVAILABLE,
                master=master,
                booking=booking,
                plan_is_pro=True,
            )

        if not updated:
            # This can happen if the row was deleted or ownership changed in between.
            return self._error(
                error=RescheduleMasterBookingError.BOOKING_NOT_FOUND,
                error_detail="update rowcount=0",
                master=master,
                booking=booking,
                plan_is_pro=True,
            )
        return None

    async def execute(self, request: RescheduleMasterBookingRequest) -> RescheduleMasterBookingResult:
        ev.info(
            "booking.reschedule_attempt",
            actor="master",
            booking_id=request.booking_id,
            master_telegram_id=request.master_telegram_id,
        )
        result: RescheduleMasterBookingResult
        try:
            self._abort_if(self._validate_ids(request))
            new_start_at_utc = self._unwrap(self._normalize_start_at(request.new_start_at_utc))
            master = self._unwrap(await self._load_master(request.master_telegram_id))
            self._unwrap(await self._ensure_pro(master=master))
            booking = self._unwrap(await self._load_booking(booking_id=request.booking_id, master=master))
            self._abort_if(self._ensure_owner(booking=booking, master=master))
            self._abort_if(self._ensure_active(booking=booking, master=master))
            self._abort_if(self._ensure_future(booking=booking, master=master))
            self._abort_if(self._ensure_changed(booking=booking, master=master, new_start_at_utc=new_start_at_utc))
            self._abort_if(
                await self._reschedule(
                    booking_id=request.booking_id,
                    master_id=master.id,
                    new_start_at_utc=new_start_at_utc,
                    master=master,
                    booking=booking,
                ),
            )
        except self._Abort as abort:
            result = abort.result
        else:
            booking = await self._booking_repo.get_for_review(request.booking_id)
            ev.info(
                "booking.rescheduled_by_master",
                booking_id=request.booking_id,
                master_id=master.id,
            )
            ev.info(
                "booking.rescheduled",
                actor="master",
                booking_id=request.booking_id,
                master_id=master.id,
            )
            result = RescheduleMasterBookingResult(
                ok=True,
                booking=booking,
                master=master,
                plan_is_pro=True,
            )

        if not result.ok:
            master_id = getattr(getattr(result, "master", None), "id", None)
            ev.info(
                "booking.reschedule_rejected",
                actor="master",
                booking_id=request.booking_id,
                master_id=int(master_id) if master_id is not None else None,
                error=str(result.error.value) if result.error else None,
            )

        return result
