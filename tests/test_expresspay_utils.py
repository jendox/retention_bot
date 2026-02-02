from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from src.integrations.expresspay.utils import default_epos_account_no


class DefaultEposAccountNoTests(unittest.TestCase):
    def test_formats_account_no_example(self) -> None:
        formed_at = datetime(2025, 12, 27, 12, 0, tzinfo=UTC)
        account_no = default_epos_account_no(123, base_account_number="01", formed_at=formed_at)
        self.assertEqual(account_no, "0127122500000000000123")

    def test_accepts_date(self) -> None:
        formed_at = date(2025, 12, 27)
        account_no = default_epos_account_no(123, base_account_number="01", formed_at=formed_at)
        self.assertEqual(account_no, "0127122500000000000123")

    def test_rejects_invalid_base_account_number(self) -> None:
        with self.assertRaises(ValueError):
            default_epos_account_no(1, base_account_number="1")
        with self.assertRaises(ValueError):
            default_epos_account_no(1, base_account_number="AA")

    def test_rejects_non_positive_master_id(self) -> None:
        with self.assertRaises(ValueError):
            default_epos_account_no(0, base_account_number="01")

    def test_rejects_too_long_master_id(self) -> None:
        # 22 - (2 + 6) = 14 digits max
        with self.assertRaises(ValueError):
            default_epos_account_no(10**14, base_account_number="01")
