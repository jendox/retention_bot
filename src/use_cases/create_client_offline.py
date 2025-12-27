from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from src.observability.events import EventLogger
from src.plans import FREE_CLIENTS_LIMIT
from src.repositories import ClientNotFound, ClientRepository, MasterNotFound, MasterRepository
from src.schemas import ClientCreate
from src.use_cases.entitlements import EntitlementsService, Usage

ev = EventLogger(__name__)


class CreateClientOfflineError(StrEnum):
    MASTER_NOT_FOUND = "master_not_found"
    QUOTA_EXCEEDED = "quota_exceeded"
    PHONE_CONFLICT = "phone_conflict"
    INVALID_REQUEST = "invalid_request"


@dataclass(frozen=True)
class CreateClientOfflinePreflightResult:
    ok: bool
    master_id: int | None = None

    # quota
    allowed: bool = False
    plan_is_pro: bool | None = None
    clients_limit: int | None = None  # None == ∞ (Pro)
    usage: Usage | None = None

    # error
    error: CreateClientOfflineError | None = None
    error_detail: str | None = None


@dataclass(frozen=True)
class CreateClientOfflineCreateResult:
    ok: bool
    master_id: int | None = None
    client_id: int | None = None

    # UX hints (после успешного создания полезно предупредить “почти лимит”)
    plan_is_pro: bool | None = None
    clients_limit: int | None = None
    usage: Usage | None = None
    warn_master_clients_near_limit: bool = False

    # error
    error: CreateClientOfflineError | None = None
    error_detail: str | None = None


class CreateClientOffline:
    def __init__(self, session: AsyncSession) -> None:
        self._client_repo = ClientRepository(session)
        self._master_repo = MasterRepository(session)
        self._entitlements = EntitlementsService(session)

    async def preflight(self, telegram_master_id: int) -> CreateClientOfflinePreflightResult:
        try:
            master = await self._master_repo.get_by_telegram_id(telegram_master_id)
        except MasterNotFound:
            ev.warning(
                "client_offline.preflight_master_not_found",
            )
            return CreateClientOfflinePreflightResult(
                ok=False,
                allowed=False,
                error=CreateClientOfflineError.MASTER_NOT_FOUND,
                error_detail="master not found",
            )

        plan = await self._entitlements.get_plan(master_id=master.id)
        if plan.is_pro:
            return CreateClientOfflinePreflightResult(
                ok=True,
                allowed=True,
                plan_is_pro=True,
                master_id=master.id,
                clients_limit=None,
            )

        usage = await self._entitlements.get_usage(master_id=master.id)
        allowed = usage.clients_count < FREE_CLIENTS_LIMIT
        return CreateClientOfflinePreflightResult(
            ok=True,
            master_id=master.id,
            plan_is_pro=False,
            allowed=allowed,
            error=None if allowed else CreateClientOfflineError.QUOTA_EXCEEDED,
            usage=usage,
            clients_limit=FREE_CLIENTS_LIMIT,
        )

    async def create(self, telegram_master_id: int, phone_e164: str, name: str) -> CreateClientOfflineCreateResult:
        result = await self.preflight(telegram_master_id)
        master_id = result.master_id
        if not result.ok:
            ev.info(
                "client_offline.create_rejected",
                error=str((result.error or CreateClientOfflineError.INVALID_REQUEST).value),
            )
            return CreateClientOfflineCreateResult(
                ok=False,
                plan_is_pro=result.plan_is_pro,
                master_id=master_id,
                error=result.error or CreateClientOfflineError.INVALID_REQUEST,
                error_detail=result.error_detail,
            )

        if master_id is None:
            ev.warning("client_offline.create_state_invalid", reason="missing_master_id")
            return CreateClientOfflineCreateResult(
                ok=False,
                plan_is_pro=result.plan_is_pro,
                master_id=master_id,
                error=CreateClientOfflineError.INVALID_REQUEST,
            )

        if not result.allowed:
            ev.info(
                "client_offline.create_rejected",
                master_id=master_id,
                error=str((result.error or CreateClientOfflineError.QUOTA_EXCEEDED).value),
            )
            return CreateClientOfflineCreateResult(
                ok=False,
                plan_is_pro=result.plan_is_pro,
                master_id=master_id,
                error=result.error or CreateClientOfflineError.QUOTA_EXCEEDED,
                usage=result.usage,
                clients_limit=result.clients_limit,
            )

        try:
            await self._client_repo.find_for_master_by_phone(
                master_id=master_id,
                phone=phone_e164,
            )
            ev.info(
                "client_offline.create_rejected",
                master_id=master_id,
                error=str(CreateClientOfflineError.PHONE_CONFLICT.value),
            )
            return CreateClientOfflineCreateResult(
                ok=False,
                plan_is_pro=result.plan_is_pro,
                master_id=master_id,
                error=CreateClientOfflineError.PHONE_CONFLICT,
                error_detail="phone conflict",
            )
        except ClientNotFound:
            pass

        client = await self._client_repo.create(
            ClientCreate(
                telegram_id=None,
                name=name,
                phone=phone_e164,
            ),
        )
        await self._master_repo.attach_client(master_id, client.id)

        warn = False
        usage: Usage | None = None
        if "clients" in await self._entitlements.near_limits(master_id=master_id):
            warn = True
            usage = await self._entitlements.get_usage(master_id=master_id)

        ev.info(
            "client.offline_created",
            master_id=master_id,
            client_id=client.id,
        )
        return CreateClientOfflineCreateResult(
            ok=True,
            master_id=master_id,
            client_id=client.id,
            plan_is_pro=result.plan_is_pro,
            warn_master_clients_near_limit=warn,
            usage=usage,
            clients_limit=result.clients_limit,
        )
