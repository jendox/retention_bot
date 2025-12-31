from __future__ import annotations

from dataclasses import dataclass

from src.use_cases.entitlements import Usage


@dataclass(frozen=True)
class BookingContext:
    booking_id: int
    master_name: str
    client_name: str
    slot_str: str  # already formatted in the recipient's timezone
    duration_min: int


@dataclass(frozen=True)
class LimitsContext:
    usage: Usage
    clients_limit: int | None = None
    bookings_limit: int | None = None


@dataclass(frozen=True)
class ReminderContext:
    master_name: str
    slot_str: str


@dataclass(frozen=True)
class OnboardingContext:
    master_name: str


@dataclass(frozen=True)
class SubscriptionContext:
    master_name: str
    plan: str  # "trial" | "pro"
    ends_on: str  # DD.MM.YYYY in master's timezone
    days_left: int
