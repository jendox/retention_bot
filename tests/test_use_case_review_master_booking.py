from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from src.use_cases.review_master_booking import (
    ReviewMasterBooking,
    ReviewMasterBookingAction,
    ReviewMasterBookingError,
    ReviewMasterBookingRequest,
)


class ReviewMasterBookingUseCaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_forbidden_when_booking_not_owned(self) -> None:
        import src.use_cases.review_master_booking as uc

        class _MasterRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(id=1)

        class _BookingRepo:
            def __init__(self, session) -> None:
                pass

            async def get_for_review(self, booking_id: int):
                return SimpleNamespace(
                    id=booking_id,
                    start_at=datetime.now(UTC) + timedelta(days=1),
                    duration_min=60,
                    master=SimpleNamespace(id=999),
                    client=SimpleNamespace(),
                )

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

        with (
            mock.patch.object(uc, "MasterRepository", _MasterRepo),
            mock.patch.object(uc, "BookingRepository", _BookingRepo),
            mock.patch.object(uc, "EntitlementsService", _Entitlements),
        ):
            result = await ReviewMasterBooking(session=object()).execute(
                ReviewMasterBookingRequest(
                    master_telegram_id=10,
                    booking_id=7,
                    action=ReviewMasterBookingAction.CONFIRM,
                ),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, ReviewMasterBookingError.FORBIDDEN)

    async def test_already_handled_when_update_returns_false(self) -> None:
        import src.use_cases.review_master_booking as uc

        class _MasterRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(id=1)

        class _BookingRepo:
            def __init__(self, session) -> None:
                pass

            async def get_for_review(self, booking_id: int):
                return SimpleNamespace(
                    id=booking_id,
                    start_at=datetime.now(UTC) + timedelta(days=1),
                    duration_min=60,
                    master=SimpleNamespace(id=1),
                    client=SimpleNamespace(),
                )

            async def set_status_if_pending_for_master(self, *, booking_id: int, master_id: int, status):
                return False

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

        with (
            mock.patch.object(uc, "MasterRepository", _MasterRepo),
            mock.patch.object(uc, "BookingRepository", _BookingRepo),
            mock.patch.object(uc, "EntitlementsService", _Entitlements),
        ):
            result = await ReviewMasterBooking(session=object()).execute(
                ReviewMasterBookingRequest(
                    master_telegram_id=10,
                    booking_id=7,
                    action=ReviewMasterBookingAction.DECLINE,
                ),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, ReviewMasterBookingError.ALREADY_HANDLED)
