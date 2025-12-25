from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from src.plans import FREE_CLIENTS_LIMIT
from src.repositories import InviteRepository, MasterNotFound, MasterRepository
from src.schemas import Invite
from src.schemas.enums import InviteType
from src.settings import get_settings
from src.use_cases.entitlements import EntitlementsService, PlanInfo, Usage


class CreateMasterClientInviteOutcome(StrEnum):
    OK = "ok"
    QUOTA_EXCEEDED = "quota_exceeded"
    MASTER_NOT_FOUND = "master_not_found"


@dataclass(frozen=True)
class CreateMasterClientInviteResult:
    outcome: CreateMasterClientInviteOutcome

    invite_link: str | None = None
    master_name: str | None = None

    plan: PlanInfo | None = None
    usage: Usage | None = None
    clients_limit: int | None = None


class CreateMasterClientInvite:
    """
    Master-side flow use-case: checks entitlements and creates a client invite link.

    Handler responsibilities:
    - UI (buttons/messages) + message GC
    - calling Notifier for limit/warn events based on returned plan/usage/limits
    """

    def __init__(self, session: AsyncSession) -> None:
        self._master_repo = MasterRepository(session)
        self._invite_repo = InviteRepository(session)
        self._entitlements = EntitlementsService(session)

    async def execute(self, *, master_telegram_id: int) -> CreateMasterClientInviteResult:
        try:
            master = await self._master_repo.get_by_telegram_id(master_telegram_id)
        except MasterNotFound:
            return CreateMasterClientInviteResult(outcome=CreateMasterClientInviteOutcome.MASTER_NOT_FOUND)

        plan = await self._entitlements.get_plan(master_id=master.id)
        usage = await self._entitlements.get_usage(master_id=master.id)
        clients_limit = None if plan.is_pro else FREE_CLIENTS_LIMIT

        allowed = bool(plan.is_pro or usage.clients_count < int(clients_limit))
        if not allowed:
            return CreateMasterClientInviteResult(
                outcome=CreateMasterClientInviteOutcome.QUOTA_EXCEEDED,
                plan=plan,
                usage=usage,
                clients_limit=clients_limit,
            )

        invite = await self._invite_repo.create(
            Invite(
                type=InviteType.CLIENT,
                master_id=master.id,
            ),
        )
        bot_username = get_settings().telegram.bot_username
        link = f"https://t.me/{bot_username}?start=c_{invite.token}"

        return CreateMasterClientInviteResult(
            outcome=CreateMasterClientInviteOutcome.OK,
            invite_link=link,
            master_name=master.name,
            plan=plan,
            usage=usage,
            clients_limit=clients_limit,
        )
