from datetime import date as date_type

from sqlalchemy import select

from src.models import WorkdayOverride as WorkdayOverrideEntity
from src.repositories.base import BaseRepository
from src.schemas import WorkdayOverride, WorkdayOverrideCreate


class WorkdayOverrideRepository(BaseRepository):
    async def get_for_master(self, master_id: int) -> list[WorkdayOverride]:
        stmt = (
            select(WorkdayOverrideEntity)
            .where(WorkdayOverrideEntity.master_id == master_id)
        )
        result = await self._session.execute(stmt)
        return [WorkdayOverride.model_validate(entity) for entity in result.scalars().all()]

    async def create(self, override: WorkdayOverrideCreate) -> WorkdayOverride:
        entity = override.to_db_entity()
        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)

        return WorkdayOverride.from_db_entity(entity)

    async def get_for_master_on_date(
        self,
        master_id: int,
        date: date_type,
    ) -> WorkdayOverride | None:
        stmt = (
            select(WorkdayOverrideEntity)
            .where(
                WorkdayOverrideEntity.master_id == master_id,
                WorkdayOverrideEntity.date == date,
            )
        )
        entity = await self._session.scalar(stmt)

        return WorkdayOverride.from_db_entity(entity) if entity else None
