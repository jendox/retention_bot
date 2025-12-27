from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.enums import InviteType

LINK_EXPIRE_MINUTES = 1440  # 24 hours


class Invite(BaseModel):
    token: str | None = Field(default=None)
    type: InviteType
    max_uses: int | None = Field(default=1)
    used_count: int = Field(default=0)
    expires_at: datetime | None = Field(default=None)
    used_at: datetime | None = Field(default=None)
    master_id: int
    client_id: int | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(
        from_attributes=True,
        extra="ignore",
    )

    @model_validator(mode="after")
    def set_token(self) -> Self:
        from src.models.invite import TOKEN_LENGTH

        if self.token is None:
            raw = secrets.token_urlsafe(TOKEN_LENGTH)
            self.token = raw[:TOKEN_LENGTH]
        return self

    @model_validator(mode="after")
    def set_expires_at(self) -> Self:
        if self.expires_at is None:
            self.expires_at = self.created_at + timedelta(minutes=LINK_EXPIRE_MINUTES)
        return self

    @classmethod
    def from_db_entity(cls, entity) -> Self:
        return cls.model_validate(entity)

    def to_db_entity(self):
        from src.models import Invite as InviteEntity

        return InviteEntity(**self.model_dump(exclude={"used_count"}))

    def is_invite_valid(self) -> bool:
        if self.max_uses is not None and self.used_count >= self.max_uses:
            return False
        if self.expires_at is not None and datetime.now(UTC) > self.expires_at:
            return False
        return True
