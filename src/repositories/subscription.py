from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.models import Subscription as SubscriptionEntity, SubscriptionPlan
from src.repositories.base import BaseRepository
from src.schemas import Subscription


class SubscriptionRepository(BaseRepository):
    async def get_by_master_id(self, master_id: int) -> Subscription | None:
        stmt = select(SubscriptionEntity).where(SubscriptionEntity.master_id == master_id)
        entity = await self._session.scalar(stmt)
        return Subscription.from_db_entity(entity) if entity is not None else None

    async def upsert_trial(self, master_id: int, trial_until: datetime) -> Subscription:
        stmt = (
            pg_insert(SubscriptionEntity)
            .values(
                master_id=master_id,
                plan=SubscriptionPlan.FREE,
                trial_until=trial_until,
            )
            .on_conflict_do_update(
                index_elements=["master_id"],
                set_={
                    "plan": SubscriptionPlan.FREE,
                    # never shorten trial
                    "trial_until": func.greatest(
                        func.coalesce(SubscriptionEntity.trial_until, trial_until),
                        trial_until,
                    ),
                },
            )
            .returning(SubscriptionEntity)
        )
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise RuntimeError("Failed to upsert trial subscription.")
        await self._session.flush()
        return Subscription.from_db_entity(entity)

    async def grant_pro(self, master_id: int, paid_until: datetime) -> Subscription:
        stmt = (
            pg_insert(SubscriptionEntity)
            .values(
                master_id=master_id,
                plan=SubscriptionPlan.PRO,
                paid_until=paid_until,
            )
            .on_conflict_do_update(
                index_elements=["master_id"],
                set_={
                    "plan": SubscriptionPlan.PRO,
                    # never shorten paid period
                    "paid_until": func.greatest(
                        func.coalesce(SubscriptionEntity.paid_until, paid_until),
                        paid_until,
                    ),
                },
            )
            .returning(SubscriptionEntity)
        )
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise RuntimeError("Failed to grant Pro subscription.")
        await self._session.flush()
        return Subscription.from_db_entity(entity)

    async def revoke_pro(self, master_id: int) -> bool:
        now = datetime.now(UTC)
        stmt = (
            update(SubscriptionEntity)
            .where(SubscriptionEntity.master_id == master_id)
            .values(
                plan=SubscriptionPlan.FREE,
                # Remove paid access; trial may still be active.
                paid_until=None,
                # If trial is already expired, clear it as well.
                trial_until=case(
                    (SubscriptionEntity.trial_until <= now, None),
                    else_=SubscriptionEntity.trial_until,
                ),
            )
        )
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0
