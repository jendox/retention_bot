from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from src.models import Invite as InviteEntity
from src.models.invite import TOKEN_LENGTH
from src.repositories.base import BaseRepository
from src.schemas import Invite

MAX_TOKEN_RETRIES = 3


class InviteNotFound(Exception): ...


class InviteRepository(BaseRepository):
    @staticmethod
    def _generate_token() -> str:
        raw = secrets.token_urlsafe(TOKEN_LENGTH)
        return raw[:TOKEN_LENGTH]

    async def create(self, invite: Invite) -> Invite:
        payload = invite.model_dump(exclude={"id", "used_count"})
        for _ in range(MAX_TOKEN_RETRIES):
            if payload.get("token") is None:
                payload["token"] = self._generate_token()
            stmt = (
                pg_insert(InviteEntity)
                .values(payload)
                .on_conflict_do_nothing(index_elements=["token"])
                .returning(
                    InviteEntity.id,
                    InviteEntity.token,
                    InviteEntity.type,
                    InviteEntity.max_uses,
                    InviteEntity.used_count,
                    InviteEntity.expires_at,
                    InviteEntity.used_at,
                    InviteEntity.master_id,
                    InviteEntity.client_id,
                    InviteEntity.created_at,
                )
            )
            try:
                row = (await self._session.execute(stmt)).first()
            except IntegrityError:
                row = None
            if row is not None:
                return Invite.model_validate(dict(row._mapping))
            payload["token"] = None
        raise RuntimeError("Failed to generate unique invite token after retries.")

    async def get_by_token(self, token: str) -> Invite:
        stmt = select(InviteEntity).where(InviteEntity.token == token)
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise InviteNotFound("Invite not found.")
        return Invite.from_db_entity(entity)

    async def increment_used_count_if_valid(self, token: str) -> bool:
        now = datetime.now(UTC)
        stmt = (
            update(InviteEntity)
            .where(InviteEntity.token == token)
            .where(
                (InviteEntity.max_uses.is_(None)) | (InviteEntity.used_count < InviteEntity.max_uses),
            )
            .where(
                (InviteEntity.expires_at.is_(None)) | (InviteEntity.expires_at > now),
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
