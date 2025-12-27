from datetime import date as date_type, time
from typing import Any, Self

from pydantic import BaseModel, ConfigDict


class WorkdayOverrideBase(BaseModel):
    master_id: int
    date: date_type
    # None = выходной
    start_time: time | None
    end_time: time | None


class WorkdayOverrideCreate(WorkdayOverrideBase):
    def to_db_entity(self):
        from src.models import WorkdayOverride as WorkdayOverrideEntity

        return WorkdayOverrideEntity(**self.model_dump())


class WorkdayOverrideUpdate(BaseModel):
    date: date_type | None = None
    start_time: time | None = None
    end_time: time | None = None

    model_config = ConfigDict(
        extra="ignore",
    )

    def to_db_update(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class WorkdayOverride(WorkdayOverrideBase):
    id: int

    model_config = ConfigDict(
        from_attributes=True,
        extra="ignore",
    )

    @classmethod
    def from_db_entity(cls, entity) -> Self:
        return cls.model_validate(entity)
