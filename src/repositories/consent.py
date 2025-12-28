from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.models.consent import UserConsent
from src.repositories.base import BaseRepository


class ConsentRepository(BaseRepository):
    async def has_consent(self, *, telegram_id: int, role: str, policy_version: str) -> bool:
        stmt = (
            select(UserConsent.telegram_id)
            .where(
                UserConsent.telegram_id == int(telegram_id),
                UserConsent.role == str(role),
                UserConsent.policy_version == str(policy_version),
            )
            .limit(1)
        )
        row = await self._session.scalar(stmt)
        return row is not None

    async def upsert_consent(
        self,
        *,
        telegram_id: int,
        role: str,
        policy_version: str,
        consented_at: datetime,
    ) -> None:
        stmt = (
            pg_insert(UserConsent)
            .values(
                telegram_id=int(telegram_id),
                role=str(role),
                policy_version=str(policy_version),
                consented_at=consented_at,
            )
            .on_conflict_do_update(
                index_elements=["telegram_id", "role"],
                set_={
                    "policy_version": str(policy_version),
                    "consented_at": consented_at,
                },
            )
        )
        await self._session.execute(stmt)

    async def delete_consent(self, *, telegram_id: int, role: str) -> bool:
        stmt = select(UserConsent).where(UserConsent.telegram_id == int(telegram_id), UserConsent.role == str(role))
        entity = await self._session.scalar(stmt)
        if entity is None:
            return False
        await self._session.delete(entity)
        return True
