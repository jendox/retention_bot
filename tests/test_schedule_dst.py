from __future__ import annotations

import unittest
from datetime import date, time
from types import SimpleNamespace

from src.schemas.enums import Timezone


class ScheduleDstTests(unittest.TestCase):
    def test_spring_forward_skips_nonexistent_local_time(self) -> None:
        from src.schedule import get_free_slots_for_date

        master = SimpleNamespace(
            slot_size_min=60,
            timezone=Timezone.EUROPE_WARSAW,
            work_window_for_day=lambda _d: (time(1, 0), time(4, 0)),
        )
        # Europe/Warsaw DST starts on 2025-03-30: local 02:00..02:59 does not exist.
        slots = get_free_slots_for_date(master=master, target_date=date(2025, 3, 30), bookings=[])
        local_hours = [s.hour for s in slots]
        self.assertEqual(local_hours, [1, 3])

    def test_fall_back_does_not_duplicate_ambiguous_local_time(self) -> None:
        from src.schedule import get_free_slots_for_date

        master = SimpleNamespace(
            slot_size_min=60,
            timezone=Timezone.EUROPE_WARSAW,
            work_window_for_day=lambda _d: (time(1, 0), time(3, 0)),
        )
        # Europe/Warsaw DST ends on 2025-10-26: local 02:00..02:59 is ambiguous.
        slots = get_free_slots_for_date(master=master, target_date=date(2025, 10, 26), bookings=[])
        local_labels = [s.strftime("%H:%M") for s in slots]
        self.assertEqual(local_labels, ["01:00", "02:00"])
        self.assertEqual(len(set(local_labels)), len(local_labels))
