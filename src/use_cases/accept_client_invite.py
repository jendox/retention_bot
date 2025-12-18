import logging
from dataclasses import dataclass, replace
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from src.repositories import (
    BookingRepository,
    ClientNotFound,
    ClientRepository,
    InviteNotFound,
    InviteRepository,
    MasterRepository,
)
from src.schemas import ClientCreate, ClientUpdate, Invite, Master, Client
from src.schemas.enums import InviteType, Timezone
from src.use_cases.entitlements import EntitlementsService, Usage

logger = logging.getLogger("accept_client_invite")


class AcceptInviteOutcome(StrEnum):
    ATTACHED_EXISTING = "attached_existing"  # клиент по telegram_id уже был
    CLAIMED_OFFLINE = "claimed_offline"  # был оффлайн-клиент у мастера по телефону -> привязали TG
    MERGED_OFFLINE = "merged_offline"  # был оффлайн-дубликат по телефону -> перекинули брони в existing
    CREATED = "created"  # создали нового клиента


class AcceptInviteError(StrEnum):
    INVITE_NOT_FOUND = "invite_not_found"
    INVITE_INVALID = "invite_invalid"  # истёк/использован (по итогам consume)
    INVITE_WRONG_TYPE = "invite_wrong_type"
    INVITE_MASTER_MISMATCH = "invite_master_mismatch"
    QUOTA_EXCEEDED = "quota_exceeded"
    PHONE_CONFLICT = "phone_conflict"  # у мастера уже есть другой TG-клиент с этим телефоном
    MISSING_PHONE = "missing_phone"  # нет phone для нового клиента и нет existing_client.phone


@dataclass(frozen=True)
class AcceptClientInviteRequest:
    telegram_id: int
    invite_token: str

    name: str | None = None
    phone_e164: str | None = None
    timezone: Timezone = Timezone.EUROPE_MINSK

    # опциональная защита от "битого state": если handler хранит master_id отдельно
    expected_master_id: int | None = None


@dataclass(frozen=True)
class AcceptClientInviteResult:
    ok: bool
    outcome: AcceptInviteOutcome | None = None

    master_id: int | None = None
    master_telegram_id: int | None = None
    client_id: int | None = None

    error: AcceptInviteError | None = None
    error_detail: str | None = None

    # можно вернуть флаг для UX (предупредить мастера)
    warn_master_clients_near_limit: bool = False
    usage: Usage | None = None


@dataclass(frozen=True)
class ValidInviteCtx:
    invite: Invite
    master_id: int


@dataclass(frozen=True)
class ResolvedClientCtx:
    master: Master
    existing_client: Client | None
    phone: str | None
    client_for_phone: Client | None


@dataclass(frozen=True)
class QuotaDecision:
    needs_quota: bool
    allowed: bool
    error: AcceptInviteError | None = None
    error_detail: str | None = None
    already_attached: bool = False


class AcceptClientInvite:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._invite_repo = InviteRepository(session)
        self._client_repo = ClientRepository(session)
        self._master_repo = MasterRepository(session)
        self._booking_repo = BookingRepository(session)
        self._entitlements = EntitlementsService(session)

    async def _get_valid_invite(self, telegram_id: int, token: str) -> tuple[Invite | None, AcceptInviteError | None]:
        try:
            invite = await self._invite_repo.get_by_token(token)
            if invite.type != InviteType.CLIENT:
                logger.warning(
                    "invite.wrong_type",
                    extra={"telegram_id": telegram_id, "invite_type": invite.type.value},
                )
                return None, AcceptInviteError.INVITE_WRONG_TYPE

            return invite, None

        except InviteNotFound:
            logger.warning(
                "invite.not_found",
                extra={"telegram_id": telegram_id, "invite_token": token})
            return None, AcceptInviteError.INVITE_NOT_FOUND

    async def _validate_invite(
        self,
        telegram_id: int,
        token: str,
    ) -> tuple[ValidInviteCtx | None, AcceptInviteError | None]:
        invite, error = await self._get_valid_invite(telegram_id, token)
        if invite is None:
            return None, error
        return ValidInviteCtx(invite=invite, master_id=invite.master_id), None

    async def _resolve_client_ctx(
        self,
        *,
        master_id: int,
        request: AcceptClientInviteRequest,
    ) -> ResolvedClientCtx | AcceptClientInviteResult:
        master = await self._master_repo.get_by_id(master_id)
        try:
            existing_client = await self._client_repo.get_by_telegram_id(request.telegram_id)
        except ClientNotFound:
            existing_client = None
        phone = request.phone_e164
        if phone is None and existing_client is None:
            logger.warning(
                "client.missing_phone",
                extra={"telegram_id": request.telegram_id, "master_telegram_id": master.telegram_id},
            )
            return AcceptClientInviteResult(
                ok=False,
                error=AcceptInviteError.MISSING_PHONE,
                master_id=master_id,
                master_telegram_id=master.telegram_id,
            )
        if phone is None and existing_client is not None:
            phone = getattr(existing_client, "phone", None)
            if phone is None:
                logger.warning(
                    "client.missing_phone",
                    extra={"telegram_id": request.telegram_id, "master_telegram_id": master.telegram_id},
                )
                return AcceptClientInviteResult(
                    ok=False,
                    error_detail=AcceptInviteError.MISSING_PHONE,
                    master_id=master_id,
                    master_telegram_id=master.telegram_id,
                )
        client_for_phone = None
        if phone is not None:
            try:
                client_for_phone = await self._client_repo.find_for_master_by_phone(
                    master_id=master_id, phone=phone,
                )
            except ClientNotFound:
                pass
        return ResolvedClientCtx(
            master=master,
            existing_client=existing_client,
            phone=phone,
            client_for_phone=client_for_phone,
        )

    async def _check_phone_conflicts(
        self,
        *,
        master_id: int,
        request: AcceptClientInviteRequest,
        resolved: ResolvedClientCtx,
    ) -> AcceptClientInviteResult | None:
        master = resolved.master
        existing_client = resolved.existing_client
        phone = resolved.phone
        client_for_phone = resolved.client_for_phone

        if client_for_phone is not None:
            tg_id = getattr(client_for_phone, "telegram_id", None)
            if tg_id is not None and tg_id != request.telegram_id:
                logger.error(
                    "client.phone_conflict",
                    extra={"phone": phone, "telegram_id": request.telegram_id, "conflict_telegram_id": tg_id},
                )
                return AcceptClientInviteResult(
                    ok=False,
                    error=AcceptInviteError.PHONE_CONFLICT,
                    master_id=master_id,
                    master_telegram_id=master.telegram_id,
                    error_detail="client_for_phone already bound to another telegram_id",
                )

        if existing_client is not None and phone is not None:
            if getattr(existing_client, "phone", None):
                try:
                    other_for_phone = await self._client_repo.find_for_master_by_phone(
                        master_id=master_id, phone=existing_client.phone,
                    )
                    if other_for_phone.id != existing_client.id:
                        if getattr(other_for_phone, "telegram_id", None) is not None \
                            and other_for_phone.telegram_id != request.telegram_id:
                            logger.error(
                                "client.phone_conflict",
                                extra={"phone": phone, "telegram_id": request.telegram_id,
                                       "conflict_telegram_id": other_for_phone.telegram_id},
                            )
                            return AcceptClientInviteResult(
                                ok=False,
                                error=AcceptInviteError.PHONE_CONFLICT,
                                master_id=master_id,
                                master_telegram_id=master.telegram_id,
                                error_detail="existing_client phone already taken by another online client for master",
                            )
                except ClientNotFound:
                    pass
        return None

    async def _decide_quota(
        self,
        *,
        master_id: int,
        request: AcceptClientInviteRequest,
        resolved: ResolvedClientCtx,
    ) -> QuotaDecision:
        existing_client = resolved.existing_client
        client_for_phone = resolved.client_for_phone

        if client_for_phone is not None:
            return QuotaDecision(
                needs_quota=False, allowed=True, already_attached=False,
            )

        if existing_client is not None:
            already_attached = await self._master_repo.is_client_attached(
                master_id=master_id, client_id=existing_client.id,
            )
            if already_attached:
                return QuotaDecision(
                    needs_quota=False, allowed=True, already_attached=True,
                )

        check = await self._entitlements.can_attach_client(master_id=master_id)
        if check.allowed:
            return QuotaDecision(
                needs_quota=True, allowed=True, already_attached=False,
            )

        logger.warning(
            "quota_exceeded",
            extra={
                "telegram_id": request.telegram_id,
                "master_id": master_id,
                "clients": f"{check.current}/{check.limit}",
            },
        )
        return QuotaDecision(
            needs_quota=True,
            allowed=False,
            error=AcceptInviteError.QUOTA_EXCEEDED,
            error_detail=f"clients={check.current}/{check.limit}",
        )

    async def _consume_invite(
        self,
        *,
        master_id: int,
        master_telegram_id: int,
        request: AcceptClientInviteRequest,
    ) -> AcceptClientInviteResult | None:
        consumed = await self._invite_repo.increment_used_count_if_valid(request.invite_token)
        if consumed:
            return None

        logger.error(
            "invite.invalid",
            extra={"telegram_id": request.telegram_id, "master_id": master_id},
        )
        return AcceptClientInviteResult(
            ok=False,
            error=AcceptInviteError.INVITE_INVALID,
            master_id=master_id,
            master_telegram_id=master_telegram_id,
        )

    async def _apply_attach(
        self,
        *,
        master_id: int,
        master_telegram_id: int,
        request: AcceptClientInviteRequest,
        resolved: ResolvedClientCtx,
    ) -> AcceptClientInviteResult:
        existing_client = resolved.existing_client
        phone = resolved.phone
        client_for_phone = resolved.client_for_phone

        if existing_client is not None and client_for_phone is not None and client_for_phone.id != existing_client.id:
            if getattr(client_for_phone, "telegram_id", None) is None:
                reassigned = await self._booking_repo.reassign_client_for_master(
                    master_id=master_id, from_client_id=client_for_phone.id, to_client_id=existing_client.id,
                )
                await self._master_repo.detach_client(master_id, client_for_phone.id)
                await self._master_repo.attach_client(master_id, existing_client.id)
                return AcceptClientInviteResult(
                    ok=True,
                    outcome=AcceptInviteOutcome.MERGED_OFFLINE,
                    master_id=master_id,
                    master_telegram_id=master_telegram_id,
                    client_id=existing_client.id,
                    error_detail=f"bookings_reassigned={reassigned}",
                )

        if existing_client is not None:
            await self._master_repo.attach_client(master_id, existing_client.id)
            return AcceptClientInviteResult(
                ok=True,
                outcome=AcceptInviteOutcome.ATTACHED_EXISTING,
                master_id=master_id,
                master_telegram_id=master_telegram_id,
                client_id=existing_client.id,
            )

        if client_for_phone is not None:
            await self._client_repo.update_by_id(
                client_for_phone.id,
                ClientUpdate(
                    telegram_id=request.telegram_id,
                    name=request.name or client_for_phone.name,
                    timezone=request.timezone,
                ),
            )
            await self._master_repo.attach_client(master_id, client_for_phone.id)
            return AcceptClientInviteResult(
                ok=True,
                outcome=AcceptInviteOutcome.CLAIMED_OFFLINE,
                master_id=master_id,
                master_telegram_id=master_telegram_id,
                client_id=client_for_phone.id,
            )

        client = await self._client_repo.create(
            ClientCreate(
                telegram_id=request.telegram_id,
                name=request.name or "Клиент",
                phone=phone,
                timezone=request.timezone,
            ),
        )
        await self._master_repo.attach_client(master_id, client.id)
        return AcceptClientInviteResult(
            ok=True,
            outcome=AcceptInviteOutcome.CREATED,
            master_id=master_id,
            master_telegram_id=master_telegram_id,
            client_id=client.id,
        )

    async def _should_warn_clients_limit(self, master_id: int) -> bool:
        close = await self._entitlements.near_limits(master_id=master_id, threshold=0.8)
        return "clients" in close

    async def execute(self, request: AcceptClientInviteRequest) -> AcceptClientInviteResult:
        valid, error = await self._validate_invite(request.telegram_id, request.invite_token)
        if valid is None:
            return AcceptClientInviteResult(ok=False, error=error)

        master_id = valid.master_id
        if request.expected_master_id is not None and request.expected_master_id != master_id:
            logger.warning(
                "invite.master_mismatch",
                extra={"master_id": master_id, "expected_master_id": request.expected_master_id},
            )
            return AcceptClientInviteResult(
                ok=False,
                error=AcceptInviteError.INVITE_MASTER_MISMATCH,
                master_id=master_id,
                error_detail=f"expected={request.expected_master_id} actual={master_id}",
            )

        resolved = await self._resolve_client_ctx(master_id=master_id, request=request)
        if isinstance(resolved, AcceptClientInviteResult):
            return resolved

        master = resolved.master
        conflict = await self._check_phone_conflicts(
            master_id=master_id, request=request, resolved=resolved,
        )
        if conflict is not None:
            return conflict

        quota = await self._decide_quota(
            master_id=master_id, request=request, resolved=resolved,
        )
        if not quota.allowed:
            return AcceptClientInviteResult(
                ok=False,
                error=quota.error,
                error_detail=quota.error_detail,
                master_id=master_id,
                master_telegram_id=resolved.master.telegram_id,
            )
        if resolved.existing_client is not None and quota.already_attached:
            if resolved.client_for_phone is None or resolved.client_for_phone.id == resolved.existing_client.id:
                invite = valid.invite
                if invite.max_uses == 1 and invite.used_count == 0 and invite.is_invite_valid():
                    consumed = await self._invite_repo.increment_used_count_if_valid(request.invite_token)
                    if not consumed:
                        logger.info(
                            "invite.noop_burn_failed",
                            extra={"telegram_id": request.telegram_id, "master_id": master_id},
                        )
                return AcceptClientInviteResult(
                    ok=True,
                    outcome=AcceptInviteOutcome.ATTACHED_EXISTING,
                    master_id=master_id,
                    master_telegram_id=resolved.master.telegram_id,
                    client_id=resolved.existing_client.id,
                )

        consume_result = await self._consume_invite(
            master_id=master_id, master_telegram_id=master.telegram_id, request=request,
        )
        if consume_result is not None:
            return consume_result

        result = await self._apply_attach(
            master_id=master_id,
            master_telegram_id=master.telegram_id,
            request=request,
            resolved=resolved,
        )

        warn = await self._should_warn_clients_limit(master_id)
        usage = await self._entitlements.get_usage(master_id=master_id) if warn else None

        return replace(result, warn_master_clients_near_limit=warn, usage=usage)
