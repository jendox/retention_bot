"""
Master registration use-cases.

This module contains two high-level operations used by the Telegram bot master registration flow:

1) StartMasterRegistration
   - Detects whether the user is already a master and whether they also exist as a client.
   - Optionally enforces invite-only registration (token must be present and valid).
   - Returns an outcome used by the handler to either show the master menu, ask for an invite, or start the FSM.

2) CompleteMasterRegistration
   - Creates a master profile (idempotent via telegram_id).
   - Schedules onboarding nudges ("add first client").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from enum import StrEnum

from sqlalchemy.exc import IntegrityError

from src.observability.events import EventLogger
from src.repositories import ClientNotFound, ClientRepository, MasterNotFound, MasterRepository
from src.repositories.scheduled_notification import ScheduledNotificationRepository
from src.schemas import MasterCreate
from src.schemas.enums import Timezone
from src.security.master_invites import verify_master_invite_token

ev = EventLogger(__name__)


class StartMasterRegistrationOutcome(StrEnum):
    ALREADY_MASTER = "already_master"
    INVITE_REQUIRED = "invite_required"
    INVITE_INVALID = "invite_invalid"
    START_FSM = "start_fsm"


@dataclass(frozen=True)
class StartMasterRegistrationRequest:
    telegram_id: int
    invite_only: bool
    invite_secret: str | None
    token: str | None = None


@dataclass(frozen=True)
class StartMasterRegistrationResult:
    outcome: StartMasterRegistrationOutcome
    is_client: bool = False


class StartMasterRegistration:
    def __init__(self, session) -> None:
        self._session = session

    async def _check_if_master(self, telegram_id: int) -> bool:
        master_repo = MasterRepository(self._session)
        try:
            await master_repo.get_by_telegram_id(telegram_id)
            return True
        except MasterNotFound:
            return False

    async def _check_if_client(self, telegram_id: int) -> bool:
        client_repo = ClientRepository(self._session)
        try:
            await client_repo.get_by_telegram_id(telegram_id)
            return True
        except ClientNotFound:
            return False

    async def execute(self, request: StartMasterRegistrationRequest) -> StartMasterRegistrationResult:
        """
        Validate whether a user can start master registration.

        The invite policy is controlled outside of this use-case via `request.invite_only` and
        `request.invite_secret` (usually derived from settings). When invite-only is enabled,
        a valid token must be provided; token claims are not used further in this flow, only
        the fact that the token is valid.
        """
        is_master = await self._check_if_master(request.telegram_id)
        is_client = await self._check_if_client(request.telegram_id)

        if is_master:
            ev.info(
                "master_registration.start_outcome",
                outcome=str(StartMasterRegistrationOutcome.ALREADY_MASTER.value),
                is_client=bool(is_client),
            )
            return StartMasterRegistrationResult(
                outcome=StartMasterRegistrationOutcome.ALREADY_MASTER,
                is_client=is_client,
            )

        if request.invite_only:
            if not request.token:
                ev.info(
                    "master_registration.start_outcome",
                    outcome=str(StartMasterRegistrationOutcome.INVITE_REQUIRED.value),
                    is_client=bool(is_client),
                )
                return StartMasterRegistrationResult(
                    outcome=StartMasterRegistrationOutcome.INVITE_REQUIRED,
                    is_client=is_client,
                )
            if not request.invite_secret:
                ev.info(
                    "master_registration.start_outcome",
                    outcome=str(StartMasterRegistrationOutcome.INVITE_INVALID.value),
                    is_client=bool(is_client),
                    reason="missing_invite_secret",
                )
                return StartMasterRegistrationResult(
                    outcome=StartMasterRegistrationOutcome.INVITE_INVALID,
                    is_client=is_client,
                )
            claims = verify_master_invite_token(secret=request.invite_secret, token=request.token)
            if claims is None:
                ev.info(
                    "master_registration.start_outcome",
                    outcome=str(StartMasterRegistrationOutcome.INVITE_INVALID.value),
                    is_client=bool(is_client),
                    reason="invalid_token",
                )
                return StartMasterRegistrationResult(
                    outcome=StartMasterRegistrationOutcome.INVITE_INVALID,
                    is_client=is_client,
                )

        ev.info(
            "master_registration.start_outcome",
            outcome=str(StartMasterRegistrationOutcome.START_FSM.value),
            is_client=bool(is_client),
        )
        return StartMasterRegistrationResult(
            outcome=StartMasterRegistrationOutcome.START_FSM,
            is_client=is_client,
        )


class CompleteMasterRegistrationOutcome(StrEnum):
    CREATED = "created"
    ALREADY_EXISTS = "already_exists"


@dataclass(frozen=True)
class CompleteMasterRegistrationRequest:
    telegram_id: int
    name: str
    phone: str
    work_days: list[int]
    start_time: time
    end_time: time
    slot_size_min: int
    timezone: Timezone = Timezone.EUROPE_MINSK


@dataclass(frozen=True)
class CompleteMasterRegistrationResult:
    outcome: CompleteMasterRegistrationOutcome
    master_id: int


class CompleteMasterRegistration:
    def __init__(self, session) -> None:
        self._session = session

    async def execute(self, request: CompleteMasterRegistrationRequest) -> CompleteMasterRegistrationResult:
        """
        Create a master profile and schedule onboarding nudges.

        This operation is idempotent for a given `telegram_id`: if the master already exists, it is loaded and
        the outcome is `ALREADY_EXISTS`.
        """
        master_repo = MasterRepository(self._session)
        outbox = ScheduledNotificationRepository(self._session)

        master_create = MasterCreate(
            telegram_id=request.telegram_id,
            name=request.name,
            phone=request.phone,
            work_days=request.work_days,
            start_time=request.start_time,
            end_time=request.end_time,
            slot_size_min=request.slot_size_min,
            timezone=request.timezone,
        )

        try:
            master = await master_repo.create(master_create)
            created = True
        except IntegrityError:
            master = await master_repo.get_by_telegram_id(request.telegram_id)
            created = False

        if created:
            now_utc = datetime.now(UTC)
            await outbox.schedule_master_onboarding_add_first_client(
                master_id=int(master.id),
                master_telegram_id=int(master.telegram_id),
                master_timezone=str(master.timezone.value),
                master_created_at=master.created_at,
                now_utc=now_utc,
            )

        ev.info(
            "master_registration.completed",
            master_id=master.id,
            outcome=str(
                CompleteMasterRegistrationOutcome.CREATED.value
                if created
                else CompleteMasterRegistrationOutcome.ALREADY_EXISTS.value,
            ),
        )
        return CompleteMasterRegistrationResult(
            outcome=CompleteMasterRegistrationOutcome.CREATED
            if created
            else CompleteMasterRegistrationOutcome.ALREADY_EXISTS,
            master_id=master.id,
        )
