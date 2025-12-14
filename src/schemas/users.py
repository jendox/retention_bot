from __future__ import annotations

import typing
from datetime import date, datetime, time
from functools import cached_property
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field

if typing.TYPE_CHECKING:
    from src.schemas import Booking
from src.schemas.enums import Timezone

# ---------- Master ----------


class MasterBase(BaseModel):
    telegram_id: int
    name: str
    work_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    start_time: time
    end_time: time
    slot_size_min: int = Field(default=60)
    timezone: Timezone = Field(default=Timezone.EUROPE_MINSK)


class MasterCreate(MasterBase):
    def to_db_entity(self):
        from src.models import Master as MasterEntity
        return MasterEntity(**self.model_dump())


class MasterUpdate(BaseModel):
    name: str | None = None
    work_days: list[int] | None = None
    start_time: time | None = None
    end_time: time | None = None
    slot_size_min: int | None = None
    timezone: Timezone | None = None

    model_config = ConfigDict(
        extra="ignore",
    )

    def to_db_update(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class Master(MasterBase):
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


# ---------- WorkdayOverride ----------

class MasterWithOverrides(Master):
    overrides: list[WorkdayOverride] = Field(default_factory=list)

    @cached_property
    def _overrides_for_day(self) -> dict[date, WorkdayOverride]:
        return {override.date: override for override in self.overrides}

    def override_for_day(self, day: date) -> WorkdayOverride | None:
        return self._overrides_for_day.get(day)

    def work_window_for_day(self, day: date) -> tuple[time, time] | None:
        # None if holiday
        override = self.override_for_day(day)

        if override is not None:
            if override.start_time is None or override.end_time is None:
                return None
            return override.start_time, override.end_time

        if day.weekday() not in self.work_days:
            return None

        return self.start_time, self.end_time


class MasterWithClients(Master):
    clients: list[Client] = Field(default_factory=list)


class WorkdayOverrideBase(BaseModel):
    master_id: int
    date: date
    # None = выходной
    start_time: time | None = None
    end_time: time | None = None


class WorkdayOverrideCreate(WorkdayOverrideBase):
    pass


class WorkdayOverride(WorkdayOverrideBase):
    id: int

    model_config = ConfigDict(
        from_attributes=True,
        extra="ignore",
    )

    @classmethod
    def from_db_entity(cls, entity) -> Self:
        return cls.model_validate(entity)


# ---------- Client ----------

class BaseClient(BaseModel):
    telegram_id: int | None = Field(default=None)
    name: str
    phone: str
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
    bookings: list["Booking"] = Field(default_factory=list)
