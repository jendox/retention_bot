from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from src import datetime_utils
from src.schemas import Booking, MasterWithOverrides


def _iter_slots_local(
    *,
    day: date,
    start_time: time,
    end_time: time,
    tz: ZoneInfo,
    step: timedelta,
) -> list[datetime]:
    start_local = datetime.combine(day, start_time, tzinfo=tz)
    end_local = datetime.combine(day, end_time, tzinfo=tz)

    slots: list[datetime] = []
    current = start_local
    while current + step <= end_local:
        slots.append(current)
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
