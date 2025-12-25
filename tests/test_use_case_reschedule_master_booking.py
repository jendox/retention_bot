from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from sqlalchemy.exc import IntegrityError

from src.use_cases.reschedule_master_booking import (
    RescheduleMasterBooking,
    RescheduleMasterBookingError,
    RescheduleMasterBookingRequest,
)


class RescheduleMasterBookingUseCaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_pro_required(self) -> None:
        import src.use_cases.reschedule_master_booking as uc

        class _MasterRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(id=1)

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

            async def get_plan(self, *, master_id: int, now=None):
                return SimpleNamespace(is_pro=False)

        class _BookingRepo:
            def __init__(self, session) -> None:
                pass

        new_start = datetime.now(UTC) + timedelta(days=1)
        with (
            mock.patch.object(uc, "MasterRepository", _MasterRepo),
            mock.patch.object(uc, "EntitlementsService", _Entitlements),
            mock.patch.object(uc, "BookingRepository", _BookingRepo),
        ):
            result = await RescheduleMasterBooking(session=object()).execute(
                RescheduleMasterBookingRequest(master_telegram_id=10, booking_id=7, new_start_at_utc=new_start),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, RescheduleMasterBookingError.PRO_REQUIRED)

    async def test_slot_not_available_maps_integrity_error(self) -> None:
        import src.use_cases.reschedule_master_booking as uc

        class _MasterRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(id=1)

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

            async def get_plan(self, *, master_id: int, now=None):
                return SimpleNamespace(is_pro=True)

        class _BookingRepo:
            def __init__(self, session) -> None:
                pass

            async def get_for_review(self, booking_id: int):
                return SimpleNamespace(
                    id=booking_id,
                    status="CONFIRMED",
                    start_at=datetime.now(UTC) + timedelta(days=1),
                    master=SimpleNamespace(id=1),
                    client=SimpleNamespace(),
                )

            async def reschedule(self, *, booking_id: int, master_id: int, start_at: datetime) -> bool:
                raise IntegrityError("x", "y", "z")

        new_start = datetime.now(UTC) + timedelta(days=2)
        with (
            mock.patch.object(uc, "MasterRepository", _MasterRepo),
            mock.patch.object(uc, "EntitlementsService", _Entitlements),
            mock.patch.object(uc, "BookingRepository", _BookingRepo),
            mock.patch.object(uc, "BookingStatus", SimpleNamespace(active=lambda: {"CONFIRMED", "PENDING"})),
        ):
            result = await RescheduleMasterBooking(session=object()).execute(
                RescheduleMasterBookingRequest(master_telegram_id=10, booking_id=7, new_start_at_utc=new_start),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, RescheduleMasterBookingError.SLOT_NOT_AVAILABLE)
