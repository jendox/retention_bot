from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from src.models import Client as ClientEntity, master_clients
from src.repositories.base import BaseRepository
from src.schemas import Client, ClientCreate, ClientDetails, ClientUpdate


class ClientNotFound(Exception): ...


class ClientRepository(BaseRepository):
    async def _get_entity_by_id(
        self,
        client_id: int,
        *,
        load_details: bool = False,
    ) -> ClientEntity:
        stmt = select(ClientEntity).where(ClientEntity.id == client_id)
        if load_details:
            stmt = stmt.options(
                selectinload(ClientEntity.masters),
                selectinload(ClientEntity.bookings),
            )
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise ClientNotFound(f"Client id={client_id} not found.")
        return entity

    async def _get_entity_by_telegram_id(
        self,
        telegram_id: int,
        *,
        load_details: bool = False,
    ) -> ClientEntity:
        stmt = select(ClientEntity).where(ClientEntity.telegram_id == telegram_id)
        if load_details:
            stmt = stmt.options(
                selectinload(ClientEntity.masters),
                selectinload(ClientEntity.bookings),
            )
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise ClientNotFound(f"Client telegram_id={telegram_id} not found.")
        return entity

    async def create(self, client: ClientCreate) -> Client:
        entity = client.to_db_entity()
        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)

        return Client.from_db_entity(entity)

    async def get_by_id(self, client_id: int) -> Client:
        entity = await self._get_entity_by_id(client_id)
        return Client.from_db_entity(entity)

    async def get_by_telegram_id(self, telegram_id: int) -> Client:
        entity = await self._get_entity_by_telegram_id(telegram_id)
        return Client.from_db_entity(entity)

    async def get_details_by_id(self, client_id: int) -> ClientDetails:
        entity = await self._get_entity_by_id(client_id, load_details=True)
        return ClientDetails.from_db_entity(entity)

    async def get_details_by_telegram_id(self, telegram_id: int) -> ClientDetails:
        entity = await self._get_entity_by_telegram_id(telegram_id, load_details=True)
        return ClientDetails.from_db_entity(entity)

    async def update_by_id(self, client_id: int, client: ClientUpdate) -> bool:
        stmt = (
            update(ClientEntity)
            .where(ClientEntity.id == client_id)
            .values(client.to_db_update())
        )
        result = await self._session.execute(stmt)

        return (result.rowcount or 0) > 0

    async def update_by_telegram_id(self, telegram_id: int, client: ClientUpdate) -> bool:
        stmt = (
            update(ClientEntity)
            .where(ClientEntity.telegram_id == telegram_id)
            .values(client.to_db_update())
        )
        result = await self._session.execute(stmt)

        return (result.rowcount or 0) > 0

    async def find_offline_for_master_by_phone(
        self,
        *,
        master_id: int,
        phone: str,
    ) -> Client:
        stmt = (
            select(ClientEntity)
            .join(master_clients, ClientEntity.id == master_clients.c.client_id)
            .where(
                master_clients.c.master_id == master_id,
                ClientEntity.phone == phone,
                ClientEntity.telegram_id.is_(None),
            )
            .limit(1)
        )
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise ClientNotFound(f"Client with phone={phone} for master_id={master_id} not found.")
        return Client.model_validate(entity)

    async def find_for_master_by_phone(
        self,
        *,
        master_id: int,
        phone: str,
    ) -> Client:
        stmt = (
            select(ClientEntity)
            .join(master_clients, ClientEntity.id == master_clients.c.client_id)
            .where(
                master_clients.c.master_id == master_id,
                ClientEntity.phone == phone,
            )
            .limit(1)
        )
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise ClientNotFound(f"Client with phone={phone} for master_id={master_id} not found.")
        return Client.model_validate(entity)
