from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from src.privacy import ConsentRole
from src.repositories.audit_log import AuditLogRepository
from src.repositories.client import ClientNotFound, ClientRepository
from src.repositories.consent import ConsentRepository
from src.repositories.master import MasterNotFound, MasterRepository


@dataclass(frozen=True)
class DeleteMasterPersonalDataResult:
    deleted: bool
    other_role_exists: bool
    audit_logs_anonymized: int


@dataclass(frozen=True)
class DeleteClientPersonalDataResult:
    deleted: bool
    other_role_exists: bool
    audit_logs_anonymized: int


class DeleteMasterPersonalData:
    def __init__(self, session: AsyncSession) -> None:
        self._masters = MasterRepository(session)
        self._clients = ClientRepository(session)
        self._consents = ConsentRepository(session)
        self._audit = AuditLogRepository(session)

    async def execute(self, *, telegram_id: int) -> DeleteMasterPersonalDataResult:
        deleted = await self._masters.delete_by_telegram_id(telegram_id)
        await self._consents.delete_consent(telegram_id=telegram_id, role=str(ConsentRole.MASTER.value))
        await self._clients.delete_orphan_offline_clients()
        audit_logs_anonymized = await self._audit.anonymize_by_actor_id(actor_id=telegram_id)

        other_role_exists = True
        try:
            await self._clients.get_by_telegram_id(telegram_id)
        except ClientNotFound:
            other_role_exists = False

        return DeleteMasterPersonalDataResult(
            deleted=bool(deleted),
            other_role_exists=bool(other_role_exists),
            audit_logs_anonymized=int(audit_logs_anonymized),
        )


class DeleteClientPersonalData:
    def __init__(self, session: AsyncSession) -> None:
        self._masters = MasterRepository(session)
        self._clients = ClientRepository(session)
        self._consents = ConsentRepository(session)
        self._audit = AuditLogRepository(session)

    async def execute(self, *, telegram_id: int) -> DeleteClientPersonalDataResult:
        deleted = await self._clients.delete_by_telegram_id(telegram_id)
        await self._consents.delete_consent(telegram_id=telegram_id, role=str(ConsentRole.CLIENT.value))
        audit_logs_anonymized = await self._audit.anonymize_by_actor_id(actor_id=telegram_id)

        other_role_exists = True
        try:
            await self._masters.get_by_telegram_id(telegram_id)
        except MasterNotFound:
            other_role_exists = False

        return DeleteClientPersonalDataResult(
            deleted=bool(deleted),
            other_role_exists=bool(other_role_exists),
            audit_logs_anonymized=int(audit_logs_anonymized),
        )
