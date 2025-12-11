import typing

from sqlalchemy import select, update
from sqlalchemy.orm import joinedload

from src.models import Master as MasterEntity
from src.repositories.base import BaseRepository
from src.schemas import Master, MasterCreate, MasterDetails, MasterUpdate

if typing.TYPE_CHECKING:
    from src.models.client import Client as ClientEntity


class MasterNotFound(Exception): ...


class MasterRepository(BaseRepository):
    async def _get_entity_by_id(
        self,
        master_id: int,
        *,
        load_details: bool = False,
    ) -> MasterEntity:
        stmt = select(MasterEntity).where(MasterEntity.id == master_id)
        if load_details:
            stmt = stmt.options(
                joinedload(MasterEntity.clients),
                joinedload(MasterEntity.bookings),
            )
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise MasterNotFound(f"Master id={master_id} not found.")
        return entity

    async def _get_entity_by_telegram_id(
        self,
        telegram_id: int,
        *,
        load_details: bool = False,
    ) -> MasterEntity:
        stmt = select(MasterEntity).where(MasterEntity.telegram_id == telegram_id)
        if load_details:
            stmt = stmt.options(
                joinedload(MasterEntity.clients),
                joinedload(MasterEntity.bookings),
            )
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

    async def get_details_by_id(self, master_id: int) -> MasterDetails:
        entity = await self._get_entity_by_id(master_id, load_details=True)
        return MasterDetails.from_db_entity(entity)

    async def get_details_by_telegram_id(self, telegram_id: int) -> MasterDetails:
        entity = await self._get_entity_by_telegram_id(telegram_id, load_details=True)
        return MasterDetails.from_db_entity(entity)

    async def update_by_id(self, master_id: int, master: MasterUpdate) -> bool:
        stmt = (
            update(MasterEntity)
            .where(MasterEntity.id == master_id)
            .values(master.to_db_update())
        )
        result = await self._session.execute(stmt)

        return (result.rowcount or 0) > 0

    async def update_by_telegram_id(self, telegram_id: int, master: MasterUpdate) -> bool:
        stmt = (
            update(MasterEntity)
            .where(MasterEntity.telegram_id == telegram_id)
            .values(master.to_db_update())
        )
        result = await self._session.execute(stmt)

        return (result.rowcount or 0) > 0

    async def attach_client(self, master_id: int, client_id: int) -> None:
        try:
            master = await self._get_entity_by_id(master_id)
            client: ClientEntity | None = await self._session.get(ClientEntity, client_id)
            if client is not None:
                master.clients.append(client)
                await self._session.flush()
        except MasterNotFound:
            pass
