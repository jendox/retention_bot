from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.repositories import ClientNotFound, ClientRepository, MasterNotFound, MasterRepository
from src.repositories.booking import BookingRepository
from src.schemas import Booking, BookingCreate, Master
from src.use_cases.entitlements import EntitlementsService, Usage

logger = logging.getLogger(__name__)


class CreateClientBookingError(StrEnum):
    MASTER_NOT_FOUND = "master_not_found"
    CLIENT_NOT_FOUND = "client_not_found"
    CLIENT_NOT_ATTACHED = "client_not_attached"
    QUOTA_EXCEEDED = "quota_exceeded"
    SLOT_NOT_AVAILABLE = "slot_not_available"
    INVALID_REQUEST = "invalid_request"


@dataclass(frozen=True)
class CreateClientBookingRequest:
    client_id: int
    master_id: int
    start_at_utc: datetime


@dataclass(frozen=True)
class CreateClientBookingResult:
    ok: bool

    booking: Booking | None = None
    master: Master | None = None

    # UX hints (после успешного создания полезно предупредить “почти лимит”)
    plan_is_pro: bool | None = None
    bookings_limit: int | None = None
    usage: Usage | None = None
    warn_master_bookings_near_limit: bool = False

    # error
    error: CreateClientBookingError | None = None
    error_detail: str | None = None


class CreateClientBooking:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._client_repo = ClientRepository(session)
        self._master_repo = MasterRepository(session)
        self._booking_repo = BookingRepository(session)
        self._entitlements = EntitlementsService(session)

    async def execute(self, request: CreateClientBookingRequest) -> CreateClientBookingResult:
        if request.client_id <= 0 or request.master_id <= 0:
            return CreateClientBookingResult(
                ok=False,
                error=CreateClientBookingError.INVALID_REQUEST,
                error_detail="client_id/master_id must be positive",
            )

        start_at_utc = request.start_at_utc
        if start_at_utc.tzinfo is None:
            return CreateClientBookingResult(
                ok=False,
                error=CreateClientBookingError.INVALID_REQUEST,
                error_detail="start_at_utc must be timezone-aware",
            )
        start_at_utc = start_at_utc.astimezone(UTC)

        now_utc = datetime.now(UTC)
        if start_at_utc <= now_utc:
            return CreateClientBookingResult(
                ok=False,
                error=CreateClientBookingError.INVALID_REQUEST,
                error_detail="start_at_utc is in the past",
            )

        try:
            master = await self._master_repo.get_by_id(request.master_id)
        except MasterNotFound:
            return CreateClientBookingResult(
                ok=False,
                error=CreateClientBookingError.MASTER_NOT_FOUND,
                error_detail=f"master_id={request.master_id}",
            )

        try:
            await self._client_repo.get_by_id(request.client_id)
        except ClientNotFound:
            return CreateClientBookingResult(
                ok=False,
                error=CreateClientBookingError.CLIENT_NOT_FOUND,
                error_detail=f"client_id={request.client_id}",
            )

        attached = await self._master_repo.is_client_attached(
            master_id=request.master_id,
            client_id=request.client_id,
        )
        if not attached:
            return CreateClientBookingResult(
                ok=False,
                error=CreateClientBookingError.CLIENT_NOT_ATTACHED,
                error_detail=f"client_id={request.client_id} not attached to master_id={request.master_id}",
            )

        check = await self._entitlements.can_create_booking(master_id=request.master_id)
        if not check.allowed:
            usage = await self._entitlements.get_usage(master_id=request.master_id)
            return CreateClientBookingResult(
                ok=False,
                master=master,
                plan_is_pro=False,
                bookings_limit=check.limit,
                usage=usage,
                error=CreateClientBookingError.QUOTA_EXCEEDED,
            )

        plan = await self._entitlements.get_plan(master_id=request.master_id)

        booking_create = BookingCreate(
            master_id=request.master_id,
            client_id=request.client_id,
            start_at=start_at_utc,
            duration_min=master.slot_size_min,
        )
        try:
            booking = await self._booking_repo.create(booking_create)
        except IntegrityError:
            return CreateClientBookingResult(
                ok=False,
                master=master,
                plan_is_pro=plan.is_pro,
                error=CreateClientBookingError.SLOT_NOT_AVAILABLE,
            )

        warn_near_limit = False
        usage: Usage | None = None
        if check.limit is not None and not plan.is_pro:
            new_count = check.current + 1
            warn_near_limit = new_count >= int(check.limit * 0.8)  # noqa: PLR2004
            if warn_near_limit:
                usage = await self._entitlements.get_usage(master_id=request.master_id)

        logger.info(
            "booking.created_by_client",
            extra={
                "booking_id": booking.id,
                "master_id": request.master_id,
                "client_id": request.client_id,
            },
        )

        return CreateClientBookingResult(
            ok=True,
            booking=booking,
            master=master,
            plan_is_pro=plan.is_pro,
            bookings_limit=check.limit,
            usage=usage,
            warn_master_bookings_near_limit=warn_near_limit,
        )
