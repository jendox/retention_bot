from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from src.observability.events import EventLogger
from src.plans import FREE_CLIENTS_LIMIT
from src.repositories import ClientNotFound, ClientRepository, MasterNotFound, MasterRepository
from src.repositories.scheduled_notification import ScheduledNotificationRepository
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
    show_offline_client_disclaimer: bool = False

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
        self._session = session
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

        show_offline_client_disclaimer = False
        if not bool(getattr(master, "offline_client_disclaimer_shown", False)):
            show_offline_client_disclaimer = True
            await self._master_repo.mark_offline_client_disclaimer_shown(int(master.id))

        plan = await self._entitlements.get_plan(master_id=master.id)
        if plan.is_pro:
            return CreateClientOfflinePreflightResult(
                ok=True,
                allowed=True,
                plan_is_pro=True,
                master_id=master.id,
                clients_limit=None,
                show_offline_client_disclaimer=show_offline_client_disclaimer,
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
            show_offline_client_disclaimer=show_offline_client_disclaimer,
        )

    async def _has_phone_conflict(self, *, master_id: int, phone_e164: str) -> bool:
        try:
            await self._client_repo.find_for_master_by_phone(
                master_id=master_id,
                phone=phone_e164,
            )
            return True
        except ClientNotFound:
            return False

    async def _maybe_schedule_onboarding_after_first_client(self, *, master_id: int, had_clients_before: bool) -> None:
        if had_clients_before:
            return

        master = await self._master_repo.get_by_id(master_id)
        outbox = ScheduledNotificationRepository(self._session)
        now_utc = datetime.now(UTC)
        await outbox.cancel_onboarding_for_master(master_id=int(master_id))
        if not bool(getattr(master, "onboarding_nudges_enabled", True)):
            return
        await outbox.schedule_master_onboarding_add_first_booking(
            master_id=int(master.id),
            master_telegram_id=int(master.telegram_id),
            master_timezone=str(master.timezone.value),
            now_utc=now_utc,
        )

    async def _near_limit_warning(self, *, master_id: int) -> tuple[bool, Usage | None]:
        if "clients" not in await self._entitlements.near_limits(master_id=master_id):
            return False, None
        return True, await self._entitlements.get_usage(master_id=master_id)

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

        if await self._has_phone_conflict(master_id=master_id, phone_e164=phone_e164):
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

        client = await self._client_repo.create(
            ClientCreate(
                telegram_id=None,
                name=name,
                phone=phone_e164,
            ),
        )
        had_clients_before = (await self._master_repo.count_clients(master_id)) > 0
        await self._master_repo.attach_client(master_id, client.id)
        await self._maybe_schedule_onboarding_after_first_client(
            master_id=master_id,
            had_clients_before=had_clients_before,
        )

        warn, usage = await self._near_limit_warning(master_id=master_id)

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
