from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from src.observability.events import EventLogger
from src.repositories import MasterNotFound, MasterRepository
from src.repositories.booking import BookingNotFound, BookingRepository
from src.schemas import BookingForReview, Master
from src.schemas.enums import AttendanceOutcome, BookingStatus

ev = EventLogger(__name__)


class MarkBookingAttendanceError(StrEnum):
    INVALID_REQUEST = "invalid_request"
    MASTER_NOT_FOUND = "master_not_found"
    BOOKING_NOT_FOUND = "booking_not_found"
    FORBIDDEN = "forbidden"
    NOT_ELIGIBLE = "not_eligible"
    ALREADY_MARKED = "already_marked"
    UPDATE_FAILED = "update_failed"


@dataclass(frozen=True)
class MarkBookingAttendanceRequest:
    master_telegram_id: int
    booking_id: int
    outcome: AttendanceOutcome


@dataclass(frozen=True)
class MarkBookingAttendanceResult:
    ok: bool

    master: Master | None = None
    booking: BookingForReview | None = None
    outcome: AttendanceOutcome | None = None

    error: MarkBookingAttendanceError | None = None
    error_detail: str | None = None


class MarkBookingAttendance:
    """
    Mark attendance outcome for a past booking.

    Safety:
    - Only booking owner can mark.
    - Only CONFIRMED bookings can be marked.
    - Only after the session end time (start_at + duration_min).
    - Outcome is write-once (UNKNOWN -> outcome) to keep UI simple for MVP.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._booking_repo = BookingRepository(session)
        self._master_repo = MasterRepository(session)

    class _Abort(Exception):
        def __init__(self, result: MarkBookingAttendanceResult) -> None:
            super().__init__("aborted")
            self.result = result

    @staticmethod
    def _error(
        *,
        error: MarkBookingAttendanceError,
        error_detail: str | None = None,
        master: Master | None = None,
        booking: BookingForReview | None = None,
        outcome: AttendanceOutcome | None = None,
    ) -> MarkBookingAttendanceResult:
        return MarkBookingAttendanceResult(
            ok=False,
            master=master,
            booking=booking,
            outcome=outcome,
            error=error,
            error_detail=error_detail,
        )

    def _unwrap(self, value):
        if isinstance(value, MarkBookingAttendanceResult):
            raise self._Abort(value)
        return value

    def _abort_if(self, maybe_error: MarkBookingAttendanceResult | None) -> None:
        if maybe_error is not None:
            raise self._Abort(maybe_error)

    @staticmethod
    def _validate(request: MarkBookingAttendanceRequest) -> MarkBookingAttendanceResult | None:
        if request.master_telegram_id <= 0 or request.booking_id <= 0:
            return MarkBookingAttendance._error(
                error=MarkBookingAttendanceError.INVALID_REQUEST,
                error_detail="master_telegram_id/booking_id must be positive",
            )
        if request.outcome not in {AttendanceOutcome.ATTENDED, AttendanceOutcome.NO_SHOW}:
            return MarkBookingAttendance._error(
                error=MarkBookingAttendanceError.INVALID_REQUEST,
                error_detail=f"unsupported outcome={request.outcome}",
            )
        return None

    async def _load_master(self, telegram_id: int) -> Master | MarkBookingAttendanceResult:
        try:
            return await self._master_repo.get_by_telegram_id(telegram_id)
        except MasterNotFound:
            return self._error(
                error=MarkBookingAttendanceError.MASTER_NOT_FOUND,
                error_detail=f"master_telegram_id={telegram_id}",
            )

    async def _load_booking(self, booking_id: int, *, master: Master) -> BookingForReview | MarkBookingAttendanceResult:
        try:
            return await self._booking_repo.get_for_review(booking_id)
        except BookingNotFound:
            return self._error(
                error=MarkBookingAttendanceError.BOOKING_NOT_FOUND,
                error_detail=f"booking_id={booking_id}",
                master=master,
            )

    def _ensure_owner(self, *, booking: BookingForReview, master: Master) -> MarkBookingAttendanceResult | None:
        if booking.master.id != master.id:
            return self._error(
                error=MarkBookingAttendanceError.FORBIDDEN,
                error_detail=f"booking.master_id={booking.master.id} != master.id={master.id}",
                master=master,
                booking=booking,
            )
        return None

    @staticmethod
    def _ensure_eligible(booking: BookingForReview) -> MarkBookingAttendanceResult | None:
        if booking.status != BookingStatus.CONFIRMED:
            return MarkBookingAttendance._error(
                error=MarkBookingAttendanceError.NOT_ELIGIBLE,
                error_detail=f"status={booking.status}",
                booking=booking,
            )

        now_utc = datetime.now(UTC)
        end_at_utc = booking.start_at.astimezone(UTC) + timedelta(minutes=int(booking.duration_min))
        if end_at_utc > now_utc:
            return MarkBookingAttendance._error(
                error=MarkBookingAttendanceError.NOT_ELIGIBLE,
                error_detail="booking has not ended yet",
                booking=booking,
            )
        return None

    async def execute(self, request: MarkBookingAttendanceRequest) -> MarkBookingAttendanceResult:
        try:
            self._abort_if(self._validate(request))
            master = self._unwrap(await self._load_master(request.master_telegram_id))
            booking = self._unwrap(await self._load_booking(request.booking_id, master=master))
            self._abort_if(self._ensure_owner(booking=booking, master=master))

            if booking.attendance_outcome != AttendanceOutcome.UNKNOWN:
                return self._error(
                    error=MarkBookingAttendanceError.ALREADY_MARKED,
                    master=master,
                    booking=booking,
                    outcome=request.outcome,
                    error_detail=f"current={booking.attendance_outcome}",
                )

            self._abort_if(self._ensure_eligible(booking))

            updated = await self._booking_repo.set_attendance_if_ended_and_confirmed(
                booking_id=request.booking_id,
                master_id=master.id,
                outcome=request.outcome,
                now_utc=datetime.now(UTC),
            )
            if not updated:
                return self._error(
                    error=MarkBookingAttendanceError.UPDATE_FAILED,
                    master=master,
                    booking=booking,
                    outcome=request.outcome,
                    error_detail="update rowcount=0",
                )
        except self._Abort as abort:
            return abort.result

        ev.info(
            "booking.attendance_marked",
            booking_id=request.booking_id,
            master_id=master.id,
            outcome=str(request.outcome.value),
        )
        return MarkBookingAttendanceResult(
            ok=True,
            master=master,
            booking=booking,
            outcome=request.outcome,
        )
