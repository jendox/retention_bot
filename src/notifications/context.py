from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BookingContext:
    booking_id: int
    master_name: str
    client_name: str
    slot_str: str  # already formatted in the recipient's timezone
    duration_min: int

