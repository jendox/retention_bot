from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo


class ScheduledNotificationQuietHoursTests(unittest.TestCase):
    def test_shift_out_of_quiet_hours_outside_quiet_returns_same_dt(self) -> None:
        from src.repositories.scheduled_notification import shift_out_of_quiet_hours

        tz = ZoneInfo("Europe/Minsk")
        dt = datetime(2025, 1, 1, 21, 59, tzinfo=tz)

        shifted = shift_out_of_quiet_hours(dt)

        self.assertEqual(shifted, dt)
        self.assertIsNotNone(shifted.tzinfo)

    def test_shift_out_of_quiet_hours_at_22_shifts_to_tomorrow_9(self) -> None:
        from src.repositories.scheduled_notification import shift_out_of_quiet_hours

        tz = ZoneInfo("Europe/Minsk")
        dt = datetime(2025, 1, 1, 22, 0, tzinfo=tz)

        shifted = shift_out_of_quiet_hours(dt)

        self.assertEqual(shifted, datetime(2025, 1, 2, 9, 0, tzinfo=tz))

    def test_shift_out_of_quiet_hours_before_9_shifts_to_today_9(self) -> None:
        from src.repositories.scheduled_notification import shift_out_of_quiet_hours

        tz = ZoneInfo("Europe/Minsk")
        dt = datetime(2025, 1, 1, 8, 59, tzinfo=tz)

        shifted = shift_out_of_quiet_hours(dt)

        self.assertEqual(shifted, datetime(2025, 1, 1, 9, 0, tzinfo=tz))

    def test_shift_out_of_quiet_hours_at_9_returns_same_dt(self) -> None:
        from src.repositories.scheduled_notification import shift_out_of_quiet_hours

        tz = ZoneInfo("Europe/Minsk")
        dt = datetime(2025, 1, 1, 9, 0, tzinfo=tz)

        shifted = shift_out_of_quiet_hours(dt)

        self.assertEqual(shifted, dt)

    def test_shift_out_of_quiet_hours_late_night_rolls_over_day(self) -> None:
        from src.repositories.scheduled_notification import shift_out_of_quiet_hours

        tz = ZoneInfo("Europe/Minsk")
        dt = datetime(2025, 1, 1, 23, 30, tzinfo=tz)

        shifted = shift_out_of_quiet_hours(dt)

        self.assertEqual(shifted, datetime(2025, 1, 2, 9, 0, tzinfo=tz))
