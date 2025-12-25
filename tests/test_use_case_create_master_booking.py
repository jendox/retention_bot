from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from sqlalchemy.exc import IntegrityError

from src.use_cases.create_master_booking import (
    CreateMasterBooking,
    CreateMasterBookingError,
    CreateMasterBookingRequest,
)
from src.use_cases.entitlements import Usage


class CreateMasterBookingUseCaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_quota_exceeded(self) -> None:
        import src.use_cases.create_master_booking as uc

        class _MasterRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_id(self, master_id: int):
                return SimpleNamespace(id=master_id, slot_size_min=60, notify_clients=True, name="M")

            async def is_client_attached(self, *, master_id: int, client_id: int) -> bool:
                return True

        class _ClientRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_id(self, client_id: int):
                return SimpleNamespace(id=client_id)

        class _BookingRepo:
            def __init__(self, session) -> None:
                pass

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

            async def can_create_booking(self, *, master_id: int):
                return SimpleNamespace(allowed=False, current=10, limit=10, reason="quota")

            async def get_usage(self, *, master_id: int, now=None):
                return Usage(clients_count=0, bookings_created_this_month=10)

        now = datetime.now(UTC) + timedelta(days=1)
        with (
            mock.patch.object(uc, "MasterRepository", _MasterRepo),
            mock.patch.object(uc, "ClientRepository", _ClientRepo),
            mock.patch.object(uc, "BookingRepository", _BookingRepo),
            mock.patch.object(uc, "EntitlementsService", _Entitlements),
        ):
            result = await CreateMasterBooking(session=object()).execute(
                CreateMasterBookingRequest(master_id=1, client_id=2, start_at_utc=now),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, CreateMasterBookingError.QUOTA_EXCEEDED)
        self.assertIsNotNone(result.usage)

    async def test_slot_not_available_maps_integrity_error(self) -> None:
        import src.use_cases.create_master_booking as uc

        class _MasterRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_id(self, master_id: int):
                return SimpleNamespace(id=master_id, slot_size_min=60, notify_clients=True, name="M")

            async def is_client_attached(self, *, master_id: int, client_id: int) -> bool:
                return True

        class _ClientRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_id(self, client_id: int):
                return SimpleNamespace(id=client_id)

        class _BookingRepo:
            def __init__(self, session) -> None:
                pass

            async def create(self, booking):
                raise IntegrityError("x", "y", "z")

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

            async def can_create_booking(self, *, master_id: int):
                return SimpleNamespace(allowed=True, current=0, limit=10, reason=None)

            async def get_plan(self, *, master_id: int, now=None):
                return SimpleNamespace(is_pro=False)

        now = datetime.now(UTC) + timedelta(days=1)
        with (
            mock.patch.object(uc, "MasterRepository", _MasterRepo),
            mock.patch.object(uc, "ClientRepository", _ClientRepo),
            mock.patch.object(uc, "BookingRepository", _BookingRepo),
            mock.patch.object(uc, "EntitlementsService", _Entitlements),
        ):
            result = await CreateMasterBooking(session=object()).execute(
                CreateMasterBookingRequest(master_id=1, client_id=2, start_at_utc=now),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, CreateMasterBookingError.SLOT_NOT_AVAILABLE)
