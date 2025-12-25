from __future__ import annotations

import unittest
from datetime import time

from src.handlers.master import register


class MasterRegisterParsersTests(unittest.TestCase):
    def test_parse_work_days_accepts_non_ascii_dash(self) -> None:
        self.assertEqual(register._parse_work_days("1–5"), [0, 1, 2, 3, 4])

    def test_parse_time_range_accepts_various_dashes(self) -> None:
        self.assertEqual(register._parse_time_range("10:00-19:00"), (time(10, 0), time(19, 0)))
        self.assertEqual(register._parse_time_range("10:00–19:00"), (time(10, 0), time(19, 0)))
        self.assertEqual(register._parse_time_range("10:00—19:00"), (time(10, 0), time(19, 0)))

    def test_parse_time_range_accepts_hours_only(self) -> None:
        self.assertEqual(register._parse_time_range("10-19"), (time(10, 0), time(19, 0)))
        self.assertEqual(register._parse_time_range("9-18"), (time(9, 0), time(18, 0)))

    def test_parse_time_range_rejects_invalid(self) -> None:
        self.assertIsNone(register._parse_time_range("19-10"))  # night shifts not supported
        self.assertIsNone(register._parse_time_range("10:00-10:00"))
        self.assertIsNone(register._parse_time_range("25-26"))
        self.assertIsNone(register._parse_time_range("10:60-11:00"))
        self.assertIsNone(register._parse_time_range("10-"))

    def test_parse_slot_size_accepts_multiples_of_five(self) -> None:
        self.assertEqual(register._parse_slot_size("5"), 5)
        self.assertEqual(register._parse_slot_size("30"), 30)
        self.assertEqual(register._parse_slot_size("55"), 55)
        self.assertEqual(register._parse_slot_size("240"), 240)

    def test_parse_slot_size_rejects_invalid(self) -> None:
        self.assertIsNone(register._parse_slot_size(""))
        self.assertIsNone(register._parse_slot_size("0"))
        self.assertIsNone(register._parse_slot_size("7"))
        self.assertIsNone(register._parse_slot_size("241"))
