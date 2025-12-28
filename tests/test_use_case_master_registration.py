from __future__ import annotations

import unittest
from datetime import UTC, datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.exc import IntegrityError

from src.schemas.enums import Timezone
from src.use_cases.master_registration import (
    CompleteMasterRegistration,
    CompleteMasterRegistrationOutcome,
    CompleteMasterRegistrationRequest,
    StartMasterRegistration,
    StartMasterRegistrationOutcome,
    StartMasterRegistrationRequest,
)


class MasterRegistrationUseCaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_registration_already_master_returns_outcome(self) -> None:
        session = object()

        master_repo = SimpleNamespace(get_by_telegram_id=AsyncMock(return_value=object()))
        client_repo = SimpleNamespace(get_by_telegram_id=AsyncMock(return_value=object()))
        with (
            patch("src.use_cases.master_registration.MasterRepository", lambda _s: master_repo),
            patch("src.use_cases.master_registration.ClientRepository", lambda _s: client_repo),
        ):
            result = await StartMasterRegistration(session).execute(
                StartMasterRegistrationRequest(
                    telegram_id=1,
                    invite_only=True,
                    invite_secret="secret",
                    token=None,
                ),
            )

        self.assertEqual(result.outcome, StartMasterRegistrationOutcome.ALREADY_MASTER)
        self.assertTrue(result.is_client)

    async def test_start_registration_invite_required(self) -> None:
        session = object()

        async def _raise_not_found(_telegram_id: int):
            from src.repositories.master import MasterNotFound

            raise MasterNotFound()

        master_repo = SimpleNamespace(get_by_telegram_id=AsyncMock(side_effect=_raise_not_found))
        client_repo = SimpleNamespace(get_by_telegram_id=AsyncMock())
        with (
            patch("src.use_cases.master_registration.MasterRepository", lambda _s: master_repo),
            patch("src.use_cases.master_registration.ClientRepository", lambda _s: client_repo),
        ):
            result = await StartMasterRegistration(session).execute(
                StartMasterRegistrationRequest(
                    telegram_id=1,
                    invite_only=True,
                    invite_secret="secret",
                    token=None,
                ),
            )

        self.assertEqual(result.outcome, StartMasterRegistrationOutcome.INVITE_REQUIRED)

    async def test_start_registration_invite_invalid(self) -> None:
        session = object()

        async def _raise_not_found(_telegram_id: int):
            from src.repositories.master import MasterNotFound

            raise MasterNotFound()

        master_repo = SimpleNamespace(get_by_telegram_id=AsyncMock(side_effect=_raise_not_found))
        client_repo = SimpleNamespace(get_by_telegram_id=AsyncMock())
        with (
            patch("src.use_cases.master_registration.MasterRepository", lambda _s: master_repo),
            patch("src.use_cases.master_registration.ClientRepository", lambda _s: client_repo),
            patch("src.use_cases.master_registration.verify_master_invite_token", lambda **_kwargs: None),
        ):
            result = await StartMasterRegistration(session).execute(
                StartMasterRegistrationRequest(
                    telegram_id=1,
                    invite_only=True,
                    invite_secret="secret",
                    token="bad",
                ),
            )

        self.assertEqual(result.outcome, StartMasterRegistrationOutcome.INVITE_INVALID)

    async def test_start_registration_start_fsm_when_public(self) -> None:
        session = object()

        async def _raise_not_found(_telegram_id: int):
            from src.repositories.master import MasterNotFound

            raise MasterNotFound()

        master_repo = SimpleNamespace(get_by_telegram_id=AsyncMock(side_effect=_raise_not_found))
        client_repo = SimpleNamespace(get_by_telegram_id=AsyncMock(return_value=object()))
        with (
            patch("src.use_cases.master_registration.MasterRepository", lambda _s: master_repo),
            patch("src.use_cases.master_registration.ClientRepository", lambda _s: client_repo),
        ):
            result = await StartMasterRegistration(session).execute(
                StartMasterRegistrationRequest(
                    telegram_id=1,
                    invite_only=False,
                    invite_secret=None,
                    token=None,
                ),
            )

        self.assertEqual(result.outcome, StartMasterRegistrationOutcome.START_FSM)
        self.assertTrue(result.is_client)

    async def test_complete_registration_created_schedules_onboarding(self) -> None:
        session = object()

        master_repo = SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(
                    id=123,
                    telegram_id=1,
                    timezone=Timezone.EUROPE_MINSK,
                    created_at=datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
                ),
            ),
        )
        outbox_repo = SimpleNamespace(schedule_master_onboarding_add_first_client=AsyncMock(return_value=3))
        with (
            patch("src.use_cases.master_registration.MasterRepository", lambda _s: master_repo),
            patch("src.use_cases.master_registration.ScheduledNotificationRepository", lambda _s: outbox_repo),
        ):
            result = await CompleteMasterRegistration(session).execute(
                CompleteMasterRegistrationRequest(
                    telegram_id=1,
                    name="Masha",
                    phone="+375291234567",
                    work_days=[0, 1, 2],
                    start_time=time(10, 0),
                    end_time=time(18, 0),
                    slot_size_min=60,
                    timezone=Timezone.EUROPE_MINSK,
                ),
            )

        self.assertEqual(result.outcome, CompleteMasterRegistrationOutcome.CREATED)
        self.assertEqual(result.master_id, 123)
        outbox_repo.schedule_master_onboarding_add_first_client.assert_awaited()

    async def test_complete_registration_integrity_error_returns_already_exists(self) -> None:
        session = object()

        async def _raise_integrity(_master_create):
            raise IntegrityError(statement=None, params=None, orig=Exception("dup"))

        master_repo = SimpleNamespace(
            create=AsyncMock(side_effect=_raise_integrity),
            get_by_telegram_id=AsyncMock(
                return_value=SimpleNamespace(
                    id=123,
                    telegram_id=1,
                    timezone=Timezone.EUROPE_MINSK,
                    created_at=datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
                ),
            ),
        )
        outbox_repo = SimpleNamespace(schedule_master_onboarding_add_first_client=AsyncMock(return_value=0))
        with (
            patch("src.use_cases.master_registration.MasterRepository", lambda _s: master_repo),
            patch("src.use_cases.master_registration.ScheduledNotificationRepository", lambda _s: outbox_repo),
        ):
            result = await CompleteMasterRegistration(session).execute(
                CompleteMasterRegistrationRequest(
                    telegram_id=1,
                    name="Masha",
                    phone="+375291234567",
                    work_days=[0, 1, 2],
                    start_time=time(10, 0),
                    end_time=time(18, 0),
                    slot_size_min=60,
                    timezone=Timezone.EUROPE_MINSK,
                ),
            )

        self.assertEqual(result.outcome, CompleteMasterRegistrationOutcome.ALREADY_EXISTS)
        self.assertEqual(result.master_id, 123)
        outbox_repo.schedule_master_onboarding_add_first_client.assert_not_awaited()
