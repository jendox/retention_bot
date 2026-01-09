from sqlalchemy import select, update

from src.models import master_clients
from src.repositories.base import BaseRepository


class MasterClientRepository(BaseRepository):
    async def get_client_aliases_for_master(self, *, master_id: int) -> dict[int, str]:
        stmt = select(master_clients.c.client_id, master_clients.c.client_alias).where(
            master_clients.c.master_id == int(master_id),
            master_clients.c.client_alias.is_not(None),
        )
        rows = (await self._session.execute(stmt)).all()
        return {int(client_id): str(alias) for client_id, alias in rows if alias is not None}

    async def get_master_aliases_for_client(self, *, client_id: int) -> dict[int, str]:
        stmt = select(master_clients.c.master_id, master_clients.c.master_alias).where(
            master_clients.c.client_id == int(client_id),
            master_clients.c.master_alias.is_not(None),
        )
        rows = (await self._session.execute(stmt)).all()
        return {int(master_id): str(alias) for master_id, alias in rows if alias is not None}

    async def get_client_alias(self, *, master_id: int, client_id: int) -> str | None:
        stmt = select(master_clients.c.client_alias).where(
            master_clients.c.master_id == int(master_id),
            master_clients.c.client_id == int(client_id),
        )
        alias = await self._session.scalar(stmt)
        return str(alias) if alias is not None else None

    async def get_master_alias(self, *, master_id: int, client_id: int) -> str | None:
        stmt = select(master_clients.c.master_alias).where(
            master_clients.c.master_id == int(master_id),
            master_clients.c.client_id == int(client_id),
        )
        alias = await self._session.scalar(stmt)
        return str(alias) if alias is not None else None

    async def set_client_alias(
        self,
        *,
        master_id: int,
        client_id: int,
        alias: str | None,
    ) -> bool:
        stmt = (
            update(master_clients)
            .where(
                master_clients.c.master_id == int(master_id),
                master_clients.c.client_id == int(client_id),
            )
            .values(client_alias=(str(alias).strip() if alias is not None else None))
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return (result.rowcount or 0) > 0

    async def set_master_alias(
        self,
        *,
        master_id: int,
        client_id: int,
        alias: str | None,
    ) -> bool:
        stmt = (
            update(master_clients)
            .where(
                master_clients.c.master_id == int(master_id),
                master_clients.c.client_id == int(client_id),
            )
            .values(master_alias=(str(alias).strip() if alias is not None else None))
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return (result.rowcount or 0) > 0

    async def set_client_alias_if_empty(
        self,
        *,
        master_id: int,
        client_id: int,
        alias: str | None,
    ) -> bool:
        if alias is None or not str(alias).strip():
            return False
        stmt = (
            update(master_clients)
            .where(
                master_clients.c.master_id == int(master_id),
                master_clients.c.client_id == int(client_id),
                master_clients.c.client_alias.is_(None),
            )
            .values(client_alias=str(alias).strip())
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return (result.rowcount or 0) > 0
