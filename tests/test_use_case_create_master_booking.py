from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock

from sqlalchemy.exc import IntegrityError

from src.schemas.enums import Timezone
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
                return SimpleNamespace(
                    id=master_id,
                    telegram_id=111,
                    timezone=Timezone.EUROPE_MINSK,
                    slot_size_min=60,
                    notify_clients=True,
                    name="M",
                )

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

            async def exists_any_for_master(self, *, master_id: int) -> bool:
                return False

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

            async def can_create_booking(self, *, master_id: int):
                return SimpleNamespace(allowed=False, current=10, limit=10, reason="quota")

            async def get_usage(self, *, master_id: int, now=None):
                return Usage(clients_count=0, bookings_created_this_month=10)

        now = datetime.now(UTC) + timedelta(days=1)
        subs_repo = SimpleNamespace(get_by_master_id=AsyncMock(return_value=None), upsert_trial=AsyncMock())
        outbox_repo = SimpleNamespace(
            cancel_onboarding_for_master=AsyncMock(return_value=0),
            schedule_trial_expiry_reminders=AsyncMock(return_value=0),
        )
        with (
            mock.patch.object(uc, "MasterRepository", _MasterRepo),
            mock.patch.object(uc, "ClientRepository", _ClientRepo),
            mock.patch.object(uc, "BookingRepository", _BookingRepo),
            mock.patch.object(uc, "EntitlementsService", _Entitlements),
            mock.patch.object(uc, "SubscriptionRepository", lambda _s: subs_repo),
            mock.patch.object(uc, "ScheduledNotificationRepository", lambda _s: outbox_repo),
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
                return SimpleNamespace(
                    id=master_id,
                    telegram_id=111,
                    timezone=Timezone.EUROPE_MINSK,
                    slot_size_min=60,
                    notify_clients=True,
                    name="M",
                )

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

            async def exists_any_for_master(self, *, master_id: int) -> bool:
                return False

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
        subs_repo = SimpleNamespace(get_by_master_id=AsyncMock(return_value=None), upsert_trial=AsyncMock())
        outbox_repo = SimpleNamespace(
            cancel_onboarding_for_master=AsyncMock(return_value=0),
            schedule_trial_expiry_reminders=AsyncMock(return_value=0),
        )
        with (
            mock.patch.object(uc, "MasterRepository", _MasterRepo),
            mock.patch.object(uc, "ClientRepository", _ClientRepo),
            mock.patch.object(uc, "BookingRepository", _BookingRepo),
            mock.patch.object(uc, "EntitlementsService", _Entitlements),
            mock.patch.object(uc, "SubscriptionRepository", lambda _s: subs_repo),
            mock.patch.object(uc, "ScheduledNotificationRepository", lambda _s: outbox_repo),
        ):
            result = await CreateMasterBooking(session=object()).execute(
                CreateMasterBookingRequest(master_id=1, client_id=2, start_at_utc=now),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, CreateMasterBookingError.SLOT_NOT_AVAILABLE)

    async def test_first_booking_starts_trial(self) -> None:
        import src.use_cases.create_master_booking as uc

        class _MasterRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_id(self, master_id: int):
                return SimpleNamespace(
                    id=master_id,
                    telegram_id=111,
                    timezone=Timezone.EUROPE_MINSK,
                    slot_size_min=60,
                    notify_clients=True,
                    name="M",
                )

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

            async def exists_any_for_master(self, *, master_id: int) -> bool:
                return False

            async def create(self, booking):
                return SimpleNamespace(id=10)

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

            async def can_create_booking(self, *, master_id: int):
                return SimpleNamespace(allowed=True, current=0, limit=10, reason=None)

            async def get_plan(self, *, master_id: int, now=None):
                return SimpleNamespace(is_pro=False)

        subs_repo = SimpleNamespace(get_by_master_id=AsyncMock(return_value=None), upsert_trial=AsyncMock())
        outbox_repo = SimpleNamespace(
            cancel_onboarding_for_master=AsyncMock(return_value=2),
            schedule_trial_expiry_reminders=AsyncMock(return_value=0),
        )

        now = datetime.now(UTC) + timedelta(days=1)
        with (
            mock.patch.object(uc, "MasterRepository", _MasterRepo),
            mock.patch.object(uc, "ClientRepository", _ClientRepo),
            mock.patch.object(uc, "BookingRepository", _BookingRepo),
            mock.patch.object(uc, "EntitlementsService", _Entitlements),
            mock.patch.object(uc, "SubscriptionRepository", lambda _s: subs_repo),
            mock.patch.object(uc, "ScheduledNotificationRepository", lambda _s: outbox_repo),
        ):
            result = await CreateMasterBooking(session=object()).execute(
                CreateMasterBookingRequest(master_id=1, client_id=2, start_at_utc=now),
            )

        self.assertTrue(result.ok)
        subs_repo.upsert_trial.assert_awaited()
        outbox_repo.schedule_trial_expiry_reminders.assert_awaited()
