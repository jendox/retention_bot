from datetime import date, datetime, time
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field

from src.models.master import Master as MasterEntity, WorkdayOverride as WorkdayOverrideEntity
from src.schemas.booking import Booking
from src.schemas.client import Client
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
    def to_db_entity(self) -> MasterEntity:
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
    def from_db_entity(cls, entity: MasterEntity) -> Self:
        return cls.model_validate(entity)


class MasterDetails(Master):
    clients: list[Client] = Field(default_factory=list)
    bookings: list[Booking] = Field(default_factory=list)


# ---------- WorkdayOverride ----------

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
    def from_db_entity(cls, entity: WorkdayOverrideEntity) -> Self:
        return cls.model_validate(entity)
