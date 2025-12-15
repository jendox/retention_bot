from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from src.models.subscription import SubscriptionPlan


class Subscription(BaseModel):
    master_id: int
    plan: SubscriptionPlan = Field(default=SubscriptionPlan.FREE)
    trial_until: datetime | None = None
    paid_until: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        extra="ignore",
    )

    @classmethod
    def from_db_entity(cls, entity) -> Self:
        return cls.model_validate(entity)
