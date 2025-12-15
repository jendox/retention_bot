from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from src.core.sa import Base


class SubscriptionPlan(StrEnum):
    FREE = "free"
    PRO = "pro"


subscription_plan_enum = ENUM(SubscriptionPlan, name="subscription_plan_enum", create_type=False)


class Subscription(Base):
    __tablename__ = "subscriptions"

    master_id: Mapped[int] = mapped_column(
        ForeignKey("masters.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    plan: Mapped[SubscriptionPlan] = mapped_column(
        subscription_plan_enum,
        default=SubscriptionPlan.FREE,
        nullable=False,
    )
    trial_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
