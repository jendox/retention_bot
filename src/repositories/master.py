from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload

from src.models import Master as MasterEntity, master_clients
from src.repositories.base import BaseRepository
from src.schemas import Master, MasterCreate, MasterUpdate, MasterWithClients, MasterWithOverrides


class MasterNotFound(Exception): ...


class MasterRepository(BaseRepository):
    async def _get_entity_by_id(self, master_id: int) -> MasterEntity:
        stmt = select(MasterEntity).where(MasterEntity.id == master_id)
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise MasterNotFound(f"Master id={master_id} not found.")
        return entity

    async def _get_entity_by_telegram_id(self, telegram_id: int) -> MasterEntity:
        stmt = select(MasterEntity).where(MasterEntity.telegram_id == telegram_id)
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise MasterNotFound(f"Master telegram_id={telegram_id} not found.")
        return entity

    async def create(self, master: MasterCreate) -> Master:
        entity = master.to_db_entity()
        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)
        return Master.from_db_entity(entity)

    async def get_by_id(self, master_id: int) -> Master:
        entity = await self._get_entity_by_id(master_id)
        return Master.from_db_entity(entity)

    async def get_by_telegram_id(self, telegram_id: int) -> Master:
        entity = await self._get_entity_by_telegram_id(telegram_id)
        return Master.from_db_entity(entity)

    async def get_with_clients_by_id(self, master_id: int) -> MasterWithClients:
        stmt = select(MasterEntity).where(MasterEntity.id == master_id).options(selectinload(MasterEntity.clients))
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise MasterNotFound(f"Master id={master_id} not found.")
        return MasterWithClients.from_db_entity(entity)

    async def get_with_clients_by_telegram_id(
        self,
        telegram_id: int,
    ) -> MasterWithClients:
        stmt = (
            select(MasterEntity)
            .where(MasterEntity.telegram_id == telegram_id)
            .options(selectinload(MasterEntity.clients))
        )
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise MasterNotFound(f"Master telegram_id={telegram_id} not found.")
        return MasterWithClients.model_validate(entity)

    async def get_for_schedule_by_id(self, master_id: int) -> MasterWithOverrides:
        stmt = select(MasterEntity).where(MasterEntity.id == master_id).options(selectinload(MasterEntity.overrides))
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise MasterNotFound(f"Master id={master_id} not found.")
        return MasterWithOverrides.model_validate(entity)

    async def get_for_schedule_by_telegram_id(self, telegram_id: int) -> MasterWithOverrides:
        stmt = (
            select(MasterEntity)
            .where(MasterEntity.telegram_id == telegram_id)
            .options(selectinload(MasterEntity.overrides))
        )
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise MasterNotFound(f"Master telegram_id={telegram_id} not found.")
        return MasterWithOverrides.model_validate(entity)

    async def update_by_id(self, master_id: int, master: MasterUpdate) -> bool:
        stmt = update(MasterEntity).where(MasterEntity.id == master_id).values(master.to_db_update())
        result = await self._session.execute(stmt)

        return (result.rowcount or 0) > 0

    async def update_by_telegram_id(self, telegram_id: int, master: MasterUpdate) -> bool:
        stmt = update(MasterEntity).where(MasterEntity.telegram_id == telegram_id).values(master.to_db_update())
        result = await self._session.execute(stmt)

        return (result.rowcount or 0) > 0

    async def attach_client(self, master_id: int, client_id: int) -> None:
        stmt = (
            pg_insert(master_clients)
            .values(master_id=master_id, client_id=client_id)
            .on_conflict_do_nothing(
                index_elements=["master_id", "client_id"],
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def detach_client(self, master_id: int, client_id: int) -> bool:
        stmt = delete(master_clients).where(
            master_clients.c.master_id == master_id,
            master_clients.c.client_id == client_id,
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return (result.rowcount or 0) > 0

    async def count_clients(self, master_id: int) -> int:
        stmt = select(func.count()).select_from(master_clients).where(master_clients.c.master_id == master_id)
        count = await self._session.scalar(stmt)
        return int(count or 0)

    async def is_client_attached(self, *, master_id: int, client_id: int) -> bool:
        stmt = (
            select(func.count())
            .select_from(master_clients)
            .where(
                master_clients.c.master_id == master_id,
                master_clients.c.client_id == client_id,
            )
        )
        count = await self._session.scalar(stmt)
        return int(count or 0) > 0
