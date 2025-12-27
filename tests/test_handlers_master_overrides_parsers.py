import unittest
from datetime import date, time


class MasterOverridesParserTests(unittest.TestCase):
    def test_parse_hhmm_accepts_valid_time(self) -> None:
        from src.handlers.master import workday_overrides as h

        self.assertEqual(h._parse_hhmm("09:30"), time(9, 30))

    def test_parse_hhmm_rejects_invalid(self) -> None:
        from src.handlers.master import workday_overrides as h

        self.assertIsNone(h._parse_hhmm("9:3"))
        self.assertIsNone(h._parse_hhmm("25:00"))
        self.assertIsNone(h._parse_hhmm("xx:yy"))

    def test_get_day_from_state_parses_iso_date(self) -> None:
        from src.handlers.master import workday_overrides as h

        self.assertEqual(h._get_day_from_state({"override_day": "2025-12-31"}), date(2025, 12, 31))
        self.assertIsNone(h._get_day_from_state({"override_day": "bad"}))
