from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.enums import AttendanceOutcome, BookingStatus
from src.schemas.users import Client, Master


class BaseBooking(BaseModel):
    master_id: int
    client_id: int
    start_at: datetime
    duration_min: int
    status: BookingStatus = Field(default=BookingStatus.PENDING)
    attendance_outcome: AttendanceOutcome = Field(default=AttendanceOutcome.UNKNOWN)


class BookingCreate(BaseBooking):
    def to_db_entity(self):
        from src.models import Booking as BookingEntity
        return BookingEntity(**self.model_dump())


class BookingUpdate(BaseModel):
    master_id: int | None = None
    client_id: int | None = None
    start_at: datetime | None = None
    duration_min: int | None = None
    status: BookingStatus | None = None

    model_config = ConfigDict(
        extra="ignore",
    )

    def to_db_update(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class Booking(BaseBooking):
    id: int
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        extra="ignore",
    )

    @classmethod
    def from_db_entity(cls, entity) -> Self:
        return cls.model_validate(entity)


class BookingForReview(Booking):
    master: Master
    client: Client
