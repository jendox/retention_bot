from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta


class ReminderWorkerTests(unittest.TestCase):
    def test_due_window_offsets_by_kind_and_tick(self) -> None:
        from src.workers.reminders import REMINDERS, due_window

        now = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
        tick = timedelta(seconds=60)
        kind_24h = next(k for k in REMINDERS if k.name == "24h")

        start, end = due_window(now_utc=now, kind=kind_24h, tick=tick)

        self.assertEqual(start, now + timedelta(hours=24))
        self.assertEqual(end, now + timedelta(hours=24, seconds=60))

    def test_dedup_key_changes_on_reschedule(self) -> None:
        from src.workers.reminders import REMINDERS, dedup_key

        kind_2h = next(k for k in REMINDERS if k.name == "2h")
        booking_id = 10
        a = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
        b = datetime(2025, 1, 1, 13, 0, tzinfo=UTC)

        key_a = dedup_key(booking_id=booking_id, start_at_utc=a, kind=kind_2h)
        key_b = dedup_key(booking_id=booking_id, start_at_utc=b, kind=kind_2h)

        self.assertNotEqual(key_a, key_b)
