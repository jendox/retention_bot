from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from src import datetime_utils
from src.schemas import Booking, MasterWithOverrides


def _localize_wall_time(*, dt_naive: datetime, tz: ZoneInfo) -> datetime | None:
    """
    Convert a naive local datetime (wall-clock) into a timezone-aware datetime.

    Handles DST edge cases:
    - Non-existent local times (spring forward) => returns None (skip slot).
    - Ambiguous local times (fall back) => uses fold=0 (first occurrence) to avoid duplicates.
    """
    if dt_naive.tzinfo is not None:
        raise ValueError("Expected naive local datetime.")

    dt0 = dt_naive.replace(tzinfo=tz, fold=0)
    back0 = dt0.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
    if back0 != dt_naive:
        # Non-existent local time.
        return None

    # For ambiguous times, fold=1 is also valid but would create duplicated wall-clock slots.
    return dt0


def _iter_slots_local(
    *,
    day: date,
    start_time: time,
    end_time: time,
    tz: ZoneInfo,
    step: timedelta,
) -> list[datetime]:
    start_naive = datetime.combine(day, start_time)
    end_naive = datetime.combine(day, end_time)

    slots: list[datetime] = []
    current = start_naive
    while current + step <= end_naive:
        localized = _localize_wall_time(dt_naive=current, tz=tz)
        if localized is not None:
            slots.append(localized)
        current += step
    return slots


def get_busy_intervals_local(
    *,
    bookings: Iterable[Booking],
    tz: ZoneInfo,
) -> list[tuple[datetime, datetime]]:
    intervals: list[tuple[datetime, datetime]] = []
    for booking in bookings:
        start = booking.start_at.astimezone(tz)
        end = (booking.start_at + timedelta(minutes=booking.duration_min)).astimezone(tz)
        intervals.append((start, end))
    return intervals


def _is_slot_free(
    *,
    slot_start: datetime,
    step: timedelta,
    busy_intervals: list[tuple[datetime, datetime]],
) -> bool:
    slot_end = slot_start + step
    for busy_start, busy_end in busy_intervals:
        if not (slot_end <= busy_start or slot_start >= busy_end):
            return False
    return True


def get_free_slots_for_date(
    *,
    master: MasterWithOverrides,
    target_date: date,  # local day for master
    bookings: Iterable[Booking],
) -> list[datetime]:
    window = master.work_window_for_day(target_date)
    if window is None:
        return []

    start_time, end_time = window
    tz = datetime_utils.get_timezone(str(master.timezone.value))
    step = timedelta(minutes=master.slot_size_min)

    slots = _iter_slots_local(
        day=target_date,
        start_time=start_time,
        end_time=end_time,
        tz=tz,
        step=step,
    )

    busy = get_busy_intervals_local(bookings=bookings, tz=tz)

    return [slot for slot in slots if _is_slot_free(slot_start=slot, step=step, busy_intervals=busy)]
