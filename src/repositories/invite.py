from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from src.models import Invite as InviteEntity
from src.repositories.base import BaseRepository
from src.schemas import Invite

MAX_TOKEN_RETRIES = 3


class InviteNotFound(Exception): ...


class InviteRepository(BaseRepository):

    async def create(self, invite: Invite) -> Invite:
        for _ in range(MAX_TOKEN_RETRIES):
            entity = invite.to_db_entity()
            self._session.add(entity)
            try:
                await self._session.flush()
                await self._session.refresh(entity)
                return Invite.from_db_entity(entity)
            except IntegrityError:
                await self._session.rollback()
                invite.token = None
        raise RuntimeError("Failed to generate unique invite token after retries.")

    async def get_by_token(self, token: str) -> Invite:
        stmt = select(InviteEntity).where(InviteEntity.token == token)
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise InviteNotFound(f"Invite token={token} not found.")
        return Invite.from_db_entity(entity)

    async def increment_used_count_if_valid(self, token: str) -> bool:
        now = datetime.now(UTC)
        stmt = (
            update(InviteEntity)
            .where(InviteEntity.token == token)
            .where(
                (InviteEntity.max_uses.is_(None)) |
                (InviteEntity.used_count < InviteEntity.max_uses),
            )
            .where(
                (InviteEntity.expires_at.is_(None)) |
                (InviteEntity.expires_at > now),
            )
            .values(
                used_count=InviteEntity.used_count + 1,
                used_at=now,
            )
        )
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0

    async def delete_expired(self) -> int:
        now = datetime.now(UTC)
        stmt = delete(InviteEntity).where(InviteEntity.expires_at <= now)
        result = await self._session.execute(stmt)

        return result.rowcount or 0
