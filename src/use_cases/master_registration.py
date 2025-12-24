from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, time
from enum import StrEnum

from sqlalchemy.exc import IntegrityError

from src.plans import TRIAL_DAYS
from src.repositories import ClientNotFound, ClientRepository, MasterNotFound, MasterRepository, SubscriptionRepository
from src.schemas import MasterCreate
from src.schemas.enums import Timezone
from src.security.master_invites import verify_master_invite_token


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

    async def execute(self, request: StartMasterRegistrationRequest) -> StartMasterRegistrationResult:
        master_repo = MasterRepository(self._session)
        client_repo = ClientRepository(self._session)

        try:
            await master_repo.get_by_telegram_id(request.telegram_id)
            is_master = True
        except MasterNotFound:
            is_master = False

        try:
            await client_repo.get_by_telegram_id(request.telegram_id)
            is_client = True
        except ClientNotFound:
            is_client = False

        if is_master:
            return StartMasterRegistrationResult(
                outcome=StartMasterRegistrationOutcome.ALREADY_MASTER,
                is_client=is_client,
            )

        if request.invite_only:
            if not request.token:
                return StartMasterRegistrationResult(
                    outcome=StartMasterRegistrationOutcome.INVITE_REQUIRED,
                    is_client=is_client,
                )
            if not request.invite_secret:
                return StartMasterRegistrationResult(
                    outcome=StartMasterRegistrationOutcome.INVITE_INVALID,
                    is_client=is_client,
                )
            claims = verify_master_invite_token(secret=request.invite_secret, token=request.token)
            if claims is None:
                return StartMasterRegistrationResult(
                    outcome=StartMasterRegistrationOutcome.INVITE_INVALID,
                    is_client=is_client,
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
        master_repo = MasterRepository(self._session)
        subscription_repo = SubscriptionRepository(self._session)

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

        if await subscription_repo.get_by_master_id(master.id) is None:
            trial_until = datetime.now(UTC) + timedelta(days=TRIAL_DAYS)
            await subscription_repo.upsert_trial(master.id, trial_until)

        return CompleteMasterRegistrationResult(
            outcome=CompleteMasterRegistrationOutcome.CREATED if created else CompleteMasterRegistrationOutcome.ALREADY_EXISTS,
            master_id=master.id,
        )
