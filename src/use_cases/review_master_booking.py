from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from src.repositories import BookingNotFound, MasterNotFound, MasterRepository
from src.repositories.booking import BookingRepository
from src.schemas import BookingForReview, Master
from src.schemas.enums import BookingStatus
from src.use_cases.entitlements import EntitlementsService


class ReviewMasterBookingAction(StrEnum):
    CONFIRM = "confirm"
    DECLINE = "decline"


class ReviewMasterBookingError(StrEnum):
    INVALID_REQUEST = "invalid_request"
    MASTER_NOT_FOUND = "master_not_found"
    BOOKING_NOT_FOUND = "booking_not_found"
    FORBIDDEN = "forbidden"
    ALREADY_HANDLED = "already_handled"
    PAST_BOOKING = "past_booking"


@dataclass(frozen=True)
class ReviewMasterBookingRequest:
    master_telegram_id: int
    booking_id: int
    action: ReviewMasterBookingAction


@dataclass(frozen=True)
class ReviewMasterBookingResult:
    ok: bool

    booking: BookingForReview | None = None
    master: Master | None = None
    new_status: BookingStatus | None = None
    plan_is_pro: bool | None = None

    error: ReviewMasterBookingError | None = None
    error_detail: str | None = None


class ReviewMasterBooking:
    """
    Confirm/decline a booking by the master.

    Safety:
    - Only the booking owner can review.
    - Only PENDING bookings can be updated (idempotent: second click => ALREADY_HANDLED).
    - Confirming past bookings is denied to avoid confusing client notifications.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._booking_repo = BookingRepository(session)
        self._master_repo = MasterRepository(session)
        self._entitlements = EntitlementsService(session)

    class _Abort(Exception):
        def __init__(self, result: ReviewMasterBookingResult) -> None:
            super().__init__("aborted")
            self.result = result

    @staticmethod
    def _error(
        *,
        error: ReviewMasterBookingError,
        error_detail: str | None = None,
        master: Master | None = None,
        booking: BookingForReview | None = None,
        plan_is_pro: bool | None = None,
        new_status: BookingStatus | None = None,
    ) -> ReviewMasterBookingResult:
        return ReviewMasterBookingResult(
            ok=False,
            master=master,
            booking=booking,
            plan_is_pro=plan_is_pro,
            new_status=new_status,
            error=error,
            error_detail=error_detail,
        )

    def _unwrap(self, value):
        if isinstance(value, ReviewMasterBookingResult):
            raise self._Abort(value)
        return value

    def _abort_if(self, maybe_error: ReviewMasterBookingResult | None) -> None:
        if maybe_error is not None:
            raise self._Abort(maybe_error)

    def _validate(self, request: ReviewMasterBookingRequest) -> ReviewMasterBookingResult | None:
        if request.master_telegram_id <= 0 or request.booking_id <= 0:
            return self._error(
                error=ReviewMasterBookingError.INVALID_REQUEST,
                error_detail="master_telegram_id/booking_id must be positive",
            )
        return None

    async def _load_master(self, telegram_id: int) -> Master | ReviewMasterBookingResult:
        try:
            return await self._master_repo.get_by_telegram_id(telegram_id)
        except MasterNotFound:
            return self._error(
                error=ReviewMasterBookingError.MASTER_NOT_FOUND,
                error_detail=f"master_telegram_id={telegram_id}",
            )

    async def _load_booking(self, booking_id: int, *, master: Master) -> BookingForReview | ReviewMasterBookingResult:
        try:
            return await self._booking_repo.get_for_review(booking_id)
        except BookingNotFound:
            return self._error(
                error=ReviewMasterBookingError.BOOKING_NOT_FOUND,
                error_detail=f"booking_id={booking_id}",
                master=master,
            )

    @staticmethod
    def _new_status(action: ReviewMasterBookingAction) -> BookingStatus:
        return BookingStatus.CONFIRMED if action == ReviewMasterBookingAction.CONFIRM else BookingStatus.DECLINED

    def _ensure_owner(self, *, booking: BookingForReview, master: Master) -> ReviewMasterBookingResult | None:
        if booking.master.id != master.id:
            return self._error(
                error=ReviewMasterBookingError.FORBIDDEN,
                error_detail=f"booking.master_id={booking.master.id} != master.id={master.id}",
                master=master,
                booking=booking,
            )
        return None

    async def execute(self, request: ReviewMasterBookingRequest) -> ReviewMasterBookingResult:
        try:
            self._abort_if(self._validate(request))
            master = self._unwrap(await self._load_master(request.master_telegram_id))
            booking = self._unwrap(await self._load_booking(request.booking_id, master=master))
            self._abort_if(self._ensure_owner(booking=booking, master=master))
            new_status = self._new_status(request.action)

            if request.action == ReviewMasterBookingAction.CONFIRM and booking.start_at <= datetime.now(UTC):
                return self._error(
                    error=ReviewMasterBookingError.PAST_BOOKING,
                    master=master,
                    booking=booking,
                    new_status=new_status,
                )

            changed = await self._booking_repo.set_status_if_pending_for_master(
                booking_id=request.booking_id,
                master_id=master.id,
                status=new_status,
            )
            if not changed:
                return self._error(
                    error=ReviewMasterBookingError.ALREADY_HANDLED,
                    master=master,
                    booking=booking,
                    new_status=new_status,
                )

            plan = await self._entitlements.get_plan(master_id=master.id)
        except self._Abort as abort:
            return abort.result

        return ReviewMasterBookingResult(
            ok=True,
            booking=booking,
            master=master,
            new_status=new_status,
            plan_is_pro=bool(plan.is_pro),
        )
