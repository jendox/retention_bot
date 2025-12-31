from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.observability.events import EventLogger
from src.plans import TRIAL_DAYS
from src.repositories import ClientNotFound, ClientRepository, MasterNotFound, MasterRepository
from src.repositories.booking import BookingRepository
from src.repositories.scheduled_notification import ScheduledNotificationRepository
from src.repositories.subscription import SubscriptionRepository
from src.schemas import Booking, BookingCreate, Client, Master
from src.schemas.enums import BookingStatus
from src.use_cases.entitlements import EntitlementsService, Usage

ev = EventLogger(__name__)


class CreateMasterBookingError(StrEnum):
    MASTER_NOT_FOUND = "master_not_found"
    CLIENT_NOT_FOUND = "client_not_found"
    CLIENT_NOT_ATTACHED = "client_not_attached"
    QUOTA_EXCEEDED = "quota_exceeded"
    SLOT_NOT_AVAILABLE = "slot_not_available"
    INVALID_REQUEST = "invalid_request"


@dataclass(frozen=True)
class CreateMasterBookingRequest:
    master_id: int
    client_id: int
    start_at_utc: datetime


@dataclass(frozen=True)
class CreateMasterBookingResult:
    ok: bool

    booking: Booking | None = None
    master: Master | None = None
    client: Client | None = None

    # UX hints (after successful creation it can be useful to warn "near limit")
    plan_is_pro: bool | None = None
    bookings_limit: int | None = None
    usage: Usage | None = None
    warn_master_bookings_near_limit: bool = False

    # error
    error: CreateMasterBookingError | None = None
    error_detail: str | None = None


class CreateMasterBooking:
    """
    Create a booking initiated by the master.

    Key properties:
    - Enforces that `start_at_utc` is timezone-aware and in the future.
    - Validates that client exists and is attached to the master.
    - Applies entitlement limits (Free bookings/month).
    - Uses DB constraints to prevent overlaps; cancelled/declined bookings do not block new bookings.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._client_repo = ClientRepository(session)
        self._master_repo = MasterRepository(session)
        self._booking_repo = BookingRepository(session)
        self._entitlements = EntitlementsService(session)
        self._subs_repo = SubscriptionRepository(session)
        self._outbox = ScheduledNotificationRepository(session)

    class _Abort(Exception):
        def __init__(self, result: CreateMasterBookingResult) -> None:
            super().__init__("aborted")
            self.result = result

    @staticmethod
    def _error(  # noqa: PLR0913
        *,
        error: CreateMasterBookingError,
        error_detail: str | None = None,
        master: Master | None = None,
        client: Client | None = None,
        plan_is_pro: bool | None = None,
        bookings_limit: int | None = None,
        usage: Usage | None = None,
    ) -> CreateMasterBookingResult:
        return CreateMasterBookingResult(
            ok=False,
            master=master,
            client=client,
            plan_is_pro=plan_is_pro,
            bookings_limit=bookings_limit,
            usage=usage,
            error=error,
            error_detail=error_detail,
        )

    def _unwrap(self, value):
        if isinstance(value, CreateMasterBookingResult):
            raise self._Abort(value)
        return value

    def _abort_if(self, maybe_error: CreateMasterBookingResult | None) -> None:
        if maybe_error is not None:
            raise self._Abort(maybe_error)

    def _validate_ids(self, request: CreateMasterBookingRequest) -> CreateMasterBookingResult | None:
        if request.client_id <= 0 or request.master_id <= 0:
            return self._error(
                error=CreateMasterBookingError.INVALID_REQUEST,
                error_detail="client_id/master_id must be positive",
            )
        return None

    def _normalize_start_at(self, start_at_utc: datetime) -> datetime | CreateMasterBookingResult:
        if start_at_utc.tzinfo is None:
            return self._error(
                error=CreateMasterBookingError.INVALID_REQUEST,
                error_detail="start_at_utc must be timezone-aware",
            )
        start_at_utc = start_at_utc.astimezone(UTC)
        if start_at_utc <= datetime.now(UTC):
            return self._error(
                error=CreateMasterBookingError.INVALID_REQUEST,
                error_detail="start_at_utc is in the past",
            )
        return start_at_utc

    async def _load_master(self, master_id: int) -> Master | CreateMasterBookingResult:
        try:
            return await self._master_repo.get_by_id(master_id)
        except MasterNotFound:
            return self._error(
                error=CreateMasterBookingError.MASTER_NOT_FOUND,
                error_detail=f"master_id={master_id}",
            )

    async def _load_client(self, *, client_id: int, master: Master) -> Client | CreateMasterBookingResult:
        try:
            return await self._client_repo.get_by_id(client_id)
        except ClientNotFound:
            return self._error(
                error=CreateMasterBookingError.CLIENT_NOT_FOUND,
                error_detail=f"client_id={client_id}",
                master=master,
            )

    async def _ensure_attached(
        self,
        *,
        master_id: int,
        client_id: int,
        master: Master,
        client: Client,
    ) -> CreateMasterBookingResult | None:
        attached = await self._master_repo.is_client_attached(
            master_id=master_id,
            client_id=client_id,
        )
        if attached:
            return None
        return self._error(
            error=CreateMasterBookingError.CLIENT_NOT_ATTACHED,
            error_detail=f"client_id={client_id} not attached to master_id={master_id}",
            master=master,
            client=client,
        )

    async def _check_quota(
        self,
        *,
        master_id: int,
        master: Master,
        client: Client,
    ) -> tuple[bool, int | None, Usage | None] | CreateMasterBookingResult:
        check = await self._entitlements.can_create_booking(master_id=master_id)
        if not check.allowed:
            usage = await self._entitlements.get_usage(master_id=master_id)
            return self._error(
                error=CreateMasterBookingError.QUOTA_EXCEEDED,
                master=master,
                client=client,
                plan_is_pro=False,
                bookings_limit=check.limit,
                usage=usage,
            )
        plan = await self._entitlements.get_plan(master_id=master_id)
        warn_near_limit = False
        usage: Usage | None = None
        if check.limit is not None and not plan.is_pro:
            new_count = check.current + 1
            warn_near_limit = new_count >= int(check.limit * 0.8)  # noqa: PLR2004
            if warn_near_limit:
                usage = await self._entitlements.get_usage(master_id=master_id)
        return plan.is_pro, check.limit, usage

    async def _create_booking(
        self,
        *,
        master_id: int,
        client_id: int,
        start_at_utc: datetime,
        master: Master,
        client: Client,
        plan_is_pro: bool,
    ) -> Booking | CreateMasterBookingResult:
        booking_create = BookingCreate(
            master_id=master_id,
            client_id=client_id,
            start_at=start_at_utc,
            duration_min=master.slot_size_min,
            status=BookingStatus.CONFIRMED,
        )
        try:
            return await self._booking_repo.create(booking_create)
        except IntegrityError:
            return self._error(
                error=CreateMasterBookingError.SLOT_NOT_AVAILABLE,
                master=master,
                client=client,
                plan_is_pro=plan_is_pro,
            )

    async def execute(self, request: CreateMasterBookingRequest) -> CreateMasterBookingResult:
        ev.info(
            "booking.create_attempt",
            actor="master",
            master_id=request.master_id,
            client_id=request.client_id,
        )
        result: CreateMasterBookingResult
        try:
            self._abort_if(self._validate_ids(request))
            start_at_utc = self._unwrap(self._normalize_start_at(request.start_at_utc))
            master = self._unwrap(await self._load_master(request.master_id))
            client = self._unwrap(await self._load_client(client_id=request.client_id, master=master))
            first_booking = not await self._booking_repo.exists_any_for_master(master_id=int(master.id))
            self._abort_if(
                await self._ensure_attached(
                    master_id=request.master_id,
                    client_id=request.client_id,
                    master=master,
                    client=client,
                ),
            )
            plan_is_pro, bookings_limit, usage = self._unwrap(
                await self._check_quota(master_id=request.master_id, master=master, client=client),
            )
            booking = self._unwrap(
                await self._create_booking(
                    master_id=request.master_id,
                    client_id=request.client_id,
                    start_at_utc=start_at_utc,
                    master=master,
                    client=client,
                    plan_is_pro=plan_is_pro,
                ),
            )
        except self._Abort as abort:
            result = abort.result
        else:
            ev.info(
                "booking.created_by_master",
                booking_id=booking.id,
                master_id=request.master_id,
                client_id=request.client_id,
            )
            ev.info(
                "booking.created",
                actor="master",
                booking_id=booking.id,
                master_id=request.master_id,
                client_id=request.client_id,
            )

            warn_near_limit = usage is not None and bookings_limit is not None and not plan_is_pro
            result = CreateMasterBookingResult(
                ok=True,
                booking=booking,
                master=master,
                client=client,
                plan_is_pro=plan_is_pro,
                bookings_limit=bookings_limit,
                usage=usage,
                warn_master_bookings_near_limit=warn_near_limit,
            )

            await self._outbox.cancel_onboarding_for_master(master_id=int(master.id))
            if first_booking and (await self._subs_repo.get_by_master_id(int(master.id)) is None):
                trial_until = datetime.now(UTC) + timedelta(days=TRIAL_DAYS)
                await self._subs_repo.upsert_trial(int(master.id), trial_until)
                ev.info(
                    "trial_started",
                    master_id=int(master.id),
                    trial_until=trial_until,
                    reason="first_booking",
                )

        if not result.ok:
            ev.info(
                "booking.create_rejected",
                actor="master",
                master_id=request.master_id,
                client_id=request.client_id,
                error=str(result.error.value) if result.error else None,
            )

        return result
