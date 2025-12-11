from __future__ import annotations

import typing
from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field

if typing.TYPE_CHECKING:
    from src.schemas import Master
from src.schemas.booking import Booking
from src.schemas.enums import Timezone


class BaseClient(BaseModel):
    telegram_id: int | None = Field(default=None)
    name: str
    phone: str | None = Field(default=None)
    timezone: Timezone = Field(default=Timezone.EUROPE_MINSK)


class ClientCreate(BaseClient):
    def to_db_entity(self):
        from src.models import Client as ClientEntity
        return ClientEntity(**self.model_dump())


class ClientUpdate(BaseModel):
    telegram_id: int | None = None
    name: str | None = None
    phone: str | None = None
    timezone: Timezone | None = None

    model_config = ConfigDict(
        extra="ignore",
    )

    def to_db_update(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class Client(BaseClient):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        extra="ignore",
    )

    @classmethod
    def from_db_entity(cls, entity) -> Self:
        return cls.model_validate(entity)


class ClientDetails(Client):
    masters: list[Master] = Field(default_factory=list)
    bookings: list[Booking] = Field(default_factory=list)
