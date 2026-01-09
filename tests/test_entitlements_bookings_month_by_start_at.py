from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace


class EntitlementsMonthlyBookingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_usage_counts_bookings_in_master_local_month_by_start_at(self) -> None:
        import src.use_cases.entitlements as ent
        from src.schemas.enums import Timezone

        captured: dict[str, datetime] = {}

        class _SubsRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_master_id(self, master_id: int):
                return None

        class _MasterRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_id(self, master_id: int):
                return SimpleNamespace(id=master_id, timezone=Timezone.EUROPE_MINSK)

            async def count_clients(self, master_id: int) -> int:
                return 0

        class _BookingRepo:
            def __init__(self, session) -> None:
                pass

            async def count_by_start_at_for_master_in_range(
                self,
                *,
                master_id: int,
                start_at_utc: datetime,
                end_at_utc: datetime,
            ):
                captured["start"] = start_at_utc
                captured["end"] = end_at_utc
                return 0

        now_utc = datetime(2025, 2, 28, 22, 30, tzinfo=UTC)  # 2025-03-01 01:30 in Europe/Minsk (UTC+3)

        with (
            unittest.mock.patch.object(ent, "SubscriptionRepository", _SubsRepo),
            unittest.mock.patch.object(ent, "MasterRepository", _MasterRepo),
            unittest.mock.patch.object(ent, "BookingRepository", _BookingRepo),
        ):
            service = ent.EntitlementsService(session=object())
            usage = await service.get_usage(master_id=1, now=now_utc)

        self.assertEqual(usage.bookings_created_this_month, 0)
        self.assertEqual(captured["start"], datetime(2025, 2, 28, 21, 0, tzinfo=UTC))
        self.assertEqual(captured["end"], datetime(2025, 3, 31, 21, 0, tzinfo=UTC))
