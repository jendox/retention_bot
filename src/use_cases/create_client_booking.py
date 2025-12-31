from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.datetime_utils import end_of_day_utc, to_zone
from src.observability.audit_log import write_audit_log
from src.observability.events import EventLogger
from src.plans import TRIAL_DAYS
from src.repositories import ClientNotFound, ClientRepository, MasterNotFound, MasterRepository
from src.repositories.booking import BookingRepository
from src.repositories.scheduled_notification import ScheduledNotificationRepository
from src.repositories.subscription import SubscriptionRepository
from src.schemas import Booking, BookingCreate, Master
from src.use_cases.entitlements import EntitlementsService, Usage

ev = EventLogger(__name__)


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
    """
    Create a booking initiated by the client.

    Properties:
    - Ensures `start_at_utc` is timezone-aware and in the future.
    - Validates that both master and client exist and are attached.
    - Applies entitlement limits (Free bookings/month).
    - Uses DB overlap constraint to prevent slot conflicts.
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
        def __init__(self, result: CreateClientBookingResult) -> None:
            super().__init__("aborted")
            self.result = result

    @staticmethod
    def _error(
        *,
        error: CreateClientBookingError,
        error_detail: str | None = None,
        master: Master | None = None,
        plan_is_pro: bool | None = None,
        bookings_limit: int | None = None,
        usage: Usage | None = None,
    ) -> CreateClientBookingResult:
        return CreateClientBookingResult(
            ok=False,
            master=master,
            plan_is_pro=plan_is_pro,
            bookings_limit=bookings_limit,
            usage=usage,
            error=error,
            error_detail=error_detail,
        )

    def _unwrap(self, value):
        if isinstance(value, CreateClientBookingResult):
            raise self._Abort(value)
        return value

    def _abort_if(self, maybe_error: CreateClientBookingResult | None) -> None:
        if maybe_error is not None:
            raise self._Abort(maybe_error)

    @staticmethod
    def _validate_ids(request: CreateClientBookingRequest) -> CreateClientBookingResult | None:
        if request.client_id <= 0 or request.master_id <= 0:
            return CreateClientBooking._error(
                error=CreateClientBookingError.INVALID_REQUEST,
                error_detail="client_id/master_id must be positive",
            )
        return None

    @staticmethod
    def _normalize_start_at(value: datetime) -> datetime | CreateClientBookingResult:
        if value.tzinfo is None:
            return CreateClientBooking._error(
                error=CreateClientBookingError.INVALID_REQUEST,
                error_detail="start_at_utc must be timezone-aware",
            )
        value = value.astimezone(UTC)
        if value <= datetime.now(UTC):
            return CreateClientBooking._error(
                error=CreateClientBookingError.INVALID_REQUEST,
                error_detail="start_at_utc is in the past",
            )
        return value

    async def _load_master(self, master_id: int) -> Master | CreateClientBookingResult:
        try:
            return await self._master_repo.get_by_id(master_id)
        except MasterNotFound:
            return self._error(
                error=CreateClientBookingError.MASTER_NOT_FOUND,
                error_detail=f"master_id={master_id}",
            )

    async def _ensure_client_exists(self, client_id: int) -> CreateClientBookingResult | None:
        try:
            await self._client_repo.get_by_id(client_id)
            return None
        except ClientNotFound:
            return self._error(
                error=CreateClientBookingError.CLIENT_NOT_FOUND,
                error_detail=f"client_id={client_id}",
            )

    async def _ensure_attached(
        self,
        *,
        master_id: int,
        client_id: int,
    ) -> CreateClientBookingResult | None:
        attached = await self._master_repo.is_client_attached(master_id=master_id, client_id=client_id)
        if attached:
            return None
        return self._error(
            error=CreateClientBookingError.CLIENT_NOT_ATTACHED,
            error_detail=f"client_id={client_id} not attached to master_id={master_id}",
        )

    async def _check_quota(
        self,
        *,
        master_id: int,
        master: Master,
    ) -> tuple[bool, int | None, Usage | None] | CreateClientBookingResult:
        check = await self._entitlements.can_create_booking(master_id=master_id)
        if not check.allowed:
            usage = await self._entitlements.get_usage(master_id=master_id)
            return self._error(
                error=CreateClientBookingError.QUOTA_EXCEEDED,
                master=master,
                plan_is_pro=False,
                bookings_limit=check.limit,
                usage=usage,
            )
        plan = await self._entitlements.get_plan(master_id=master_id)
        usage: Usage | None = None
        warn_near_limit = False
        if check.limit is not None and not plan.is_pro:
            new_count = check.current + 1
            warn_near_limit = new_count >= int(check.limit * 0.8)  # noqa: PLR2004
            if warn_near_limit:
                usage = await self._entitlements.get_usage(master_id=master_id)
        return bool(plan.is_pro), check.limit, usage

    async def _create_booking(
        self,
        *,
        master: Master,
        master_id: int,
        client_id: int,
        start_at_utc: datetime,
        plan_is_pro: bool,
    ) -> Booking | CreateClientBookingResult:
        booking_create = BookingCreate(
            master_id=master_id,
            client_id=client_id,
            start_at=start_at_utc,
            duration_min=master.slot_size_min,
        )
        try:
            return await self._booking_repo.create(booking_create)
        except IntegrityError:
            return self._error(
                error=CreateClientBookingError.SLOT_NOT_AVAILABLE,
                master=master,
                plan_is_pro=plan_is_pro,
            )

    async def execute(self, request: CreateClientBookingRequest) -> CreateClientBookingResult:
        ev.info(
            "booking.create_attempt",
            actor="client",
            master_id=request.master_id,
            client_id=request.client_id,
        )
        result: CreateClientBookingResult
        try:
            self._abort_if(self._validate_ids(request))
            start_at_utc = self._unwrap(self._normalize_start_at(request.start_at_utc))
            master = self._unwrap(await self._load_master(request.master_id))
            first_booking = not await self._booking_repo.exists_any_for_master(master_id=int(master.id))
            self._abort_if(await self._ensure_client_exists(request.client_id))
            self._abort_if(await self._ensure_attached(master_id=request.master_id, client_id=request.client_id))
            plan_is_pro, bookings_limit, usage = self._unwrap(
                await self._check_quota(master_id=request.master_id, master=master),
            )
            booking = self._unwrap(
                await self._create_booking(
                    master=master,
                    master_id=request.master_id,
                    client_id=request.client_id,
                    start_at_utc=start_at_utc,
                    plan_is_pro=plan_is_pro,
                ),
            )
        except self._Abort as abort:
            result = abort.result
        else:
            ev.info(
                "booking.created_by_client",
                booking_id=booking.id,
                master_id=request.master_id,
                client_id=request.client_id,
            )
            ev.info(
                "booking.created",
                actor="client",
                booking_id=booking.id,
                master_id=request.master_id,
                client_id=request.client_id,
            )

            warn_near_limit = usage is not None and bookings_limit is not None and not plan_is_pro
            result = CreateClientBookingResult(
                ok=True,
                booking=booking,
                master=master,
                plan_is_pro=plan_is_pro,
                bookings_limit=bookings_limit,
                usage=usage,
                warn_master_bookings_near_limit=warn_near_limit,
            )

            await self._outbox.cancel_onboarding_for_master(master_id=int(master.id))
            if first_booking and (await self._subs_repo.get_by_master_id(int(master.id)) is None):
                now_utc = datetime.now(UTC)
                local_now = to_zone(now_utc, master.timezone)
                end_day = local_now.date() + timedelta(days=TRIAL_DAYS - 1)
                trial_until = end_of_day_utc(day=end_day, tz=master.timezone)
                await self._subs_repo.upsert_trial(int(master.id), trial_until)
                await self._outbox.schedule_trial_expiry_reminders(
                    master_id=int(master.id),
                    master_telegram_id=int(master.telegram_id),
                    master_timezone=str(master.timezone.value),
                    trial_until_utc=trial_until,
                    now_utc=now_utc,
                )
                ev.info(
                    "trial_started",
                    master_id=int(master.id),
                    trial_until=trial_until,
                    reason="first_booking",
                )
                write_audit_log(
                    self._session,
                    event="trial_started",
                    actor="system",
                    master_id=int(master.id),
                    metadata={"trial_until": trial_until, "reason": "first_booking"},
                )

        if not result.ok:
            ev.info(
                "booking.create_rejected",
                actor="client",
                master_id=request.master_id,
                client_id=request.client_id,
                error=str(result.error.value) if result.error else None,
            )

        return result
