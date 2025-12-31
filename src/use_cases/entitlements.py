from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.subscription import SubscriptionPlan
from src.plans import (
    FREE_BOOKING_HORIZON_DAYS,
    FREE_BOOKINGS_PER_MONTH_LIMIT,
    FREE_CLIENTS_LIMIT,
    PRO_BOOKING_HORIZON_DAYS,
)
from src.repositories import BookingRepository, MasterRepository
from src.repositories.subscription import SubscriptionRepository

EntitlementSource = Literal["paid", "trial", "pro", "free"]


@dataclass(frozen=True)
class PlanInfo:
    plan: SubscriptionPlan
    is_pro: bool
    source: EntitlementSource
    active_until: datetime | None


@dataclass(frozen=True)
class Usage:
    clients_count: int
    bookings_created_this_month: int


@dataclass(frozen=True)
class EntitlementCheck:
    allowed: bool
    reason: str | None
    current: int
    limit: int | None
    remaining: int | None


def _month_bounds_utc(now: datetime) -> tuple[datetime, datetime]:
    if now.tzinfo is None:
        raise ValueError("Expected timezone-aware datetime in UTC.")
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    if now.month == 12:  # noqa: PLR2004
        end = datetime(now.year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(now.year, now.month + 1, 1, tzinfo=UTC)
    return start, end


class EntitlementsService:
    """
    Central place for Free/Pro checks.

    This service is read-only (uses the provided session) and is meant to be called
    from handlers/use-cases to consistently enforce limits.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._subs_repo = SubscriptionRepository(session)
        self._master_repo = MasterRepository(session)
        self._booking_repo = BookingRepository(session)

    async def get_plan(self, *, master_id: int, now: datetime | None = None) -> PlanInfo:
        now_utc = now or datetime.now(UTC)
        sub = await self._subs_repo.get_by_master_id(master_id)

        if sub is None:
            return PlanInfo(
                plan=SubscriptionPlan.FREE,
                is_pro=False,
                source="free",
                active_until=None,
            )

        trial_active = bool(sub.trial_until and sub.trial_until > now_utc)
        paid_active = bool(sub.paid_until and sub.paid_until > now_utc)
        plan_is_pro = sub.plan == SubscriptionPlan.PRO
        lifetime_pro = plan_is_pro and sub.paid_until is None and sub.trial_until is None

        if paid_active:
            return PlanInfo(plan=sub.plan, is_pro=True, source="paid", active_until=sub.paid_until)
        if trial_active:
            return PlanInfo(plan=sub.plan, is_pro=True, source="trial", active_until=sub.trial_until)
        if lifetime_pro:
            return PlanInfo(plan=sub.plan, is_pro=True, source="pro", active_until=None)

        return PlanInfo(plan=sub.plan, is_pro=False, source="free", active_until=None)

    async def get_plan_for_telegram(self, *, master_telegram_id: int, now: datetime | None = None) -> PlanInfo:
        master = await self._master_repo.get_by_telegram_id(master_telegram_id)
        return await self.get_plan(master_id=master.id, now=now)

    async def get_usage(self, *, master_id: int, now: datetime | None = None) -> Usage:
        now_utc = now or datetime.now(UTC)
        month_start, month_end = _month_bounds_utc(now_utc)

        clients_count = await self._master_repo.count_clients(master_id)
        bookings_count = await self._booking_repo.count_created_for_master_in_range(
            master_id=master_id,
            start_at_utc=month_start,
            end_at_utc=month_end,
        )
        return Usage(
            clients_count=clients_count,
            bookings_created_this_month=bookings_count,
        )

    async def can_attach_client(self, *, master_id: int, now: datetime | None = None) -> EntitlementCheck:
        plan = await self.get_plan(master_id=master_id, now=now)
        usage = await self.get_usage(master_id=master_id, now=now)

        if plan.is_pro:
            return EntitlementCheck(
                allowed=True,
                reason=None,
                current=usage.clients_count,
                limit=None,
                remaining=None,
            )

        limit = FREE_CLIENTS_LIMIT
        remaining = max(0, limit - usage.clients_count)
        allowed = usage.clients_count < limit
        return EntitlementCheck(
            allowed=allowed,
            reason=None if allowed else "clients_limit_reached",
            current=usage.clients_count,
            limit=limit,
            remaining=remaining,
        )

    async def can_create_booking(self, *, master_id: int, now: datetime | None = None) -> EntitlementCheck:
        plan = await self.get_plan(master_id=master_id, now=now)
        usage = await self.get_usage(master_id=master_id, now=now)

        if plan.is_pro:
            return EntitlementCheck(
                allowed=True,
                reason=None,
                current=usage.bookings_created_this_month,
                limit=None,
                remaining=None,
            )

        limit = FREE_BOOKINGS_PER_MONTH_LIMIT
        remaining = max(0, limit - usage.bookings_created_this_month)
        allowed = usage.bookings_created_this_month < limit
        return EntitlementCheck(
            allowed=allowed,
            reason=None if allowed else "bookings_month_limit_reached",
            current=usage.bookings_created_this_month,
            limit=limit,
            remaining=remaining,
        )

    async def max_booking_horizon_days(self, *, master_id: int, now: datetime | None = None) -> int:
        plan = await self.get_plan(master_id=master_id, now=now)
        return PRO_BOOKING_HORIZON_DAYS if plan.is_pro else FREE_BOOKING_HORIZON_DAYS

    async def near_limits(
        self,
        *,
        master_id: int,
        threshold: float = 0.8,
        now: datetime | None = None,
    ) -> set[str]:
        """
        Returns a set of limit names ("clients", "bookings") that are close to exhaustion.
        Intended for UX warnings (e.g., at 80% usage).
        """
        if threshold <= 0 or threshold >= 1:
            raise ValueError("threshold must be between 0 and 1 (exclusive).")

        plan = await self.get_plan(master_id=master_id, now=now)
        if plan.is_pro:
            return set()

        usage = await self.get_usage(master_id=master_id, now=now)
        close: set[str] = set()

        if usage.clients_count >= int(FREE_CLIENTS_LIMIT * threshold):
            close.add("clients")
        if usage.bookings_created_this_month >= int(FREE_BOOKINGS_PER_MONTH_LIMIT * threshold):
            close.add("bookings")

        return close
