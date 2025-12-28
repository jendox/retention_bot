from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.use_cases.master_registration import StartMasterRegistrationOutcome, StartMasterRegistrationResult


class MemoryState:
    def __init__(self) -> None:
        self._data: dict = {}
        self._state = None

    async def get_data(self) -> dict:
        return dict(self._data)

    async def update_data(self, **kwargs) -> None:
        self._data.update(kwargs)

    async def set_state(self, state) -> None:
        self._state = state

    async def clear(self) -> None:
        self._data = {}
        self._state = None


@asynccontextmanager
async def _fake_session_local():
    yield object()


@asynccontextmanager
async def _fake_active_session():
    yield object()


class MasterRegistrationPersonalDataTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_registration_without_consent_shows_consent_screen(self) -> None:
        from src.handlers.master import register as h

        state = MemoryState()
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(),
            text="/start",
        )

        class _StartUC:
            def __init__(self, session) -> None:
                pass

            async def execute(self, request) -> StartMasterRegistrationResult:  # noqa: ARG001
                return StartMasterRegistrationResult(outcome=StartMasterRegistrationOutcome.START_FSM, is_client=False)

        class _ConsentRepo:
            def __init__(self, session) -> None:
                pass

            async def has_consent(self, *, telegram_id: int, role: str, policy_version: str) -> bool:  # noqa: ARG002
                return False

        settings = SimpleNamespace(
            billing=SimpleNamespace(contact="@admin"),
            security=SimpleNamespace(master_invite_secret=None, master_public_registration=True),
        )

        answer = AsyncMock()
        with (
            patch.object(h, "get_settings", lambda: settings),
            patch.object(h, "session_local", _fake_session_local),
            patch.object(h, "StartMasterRegistration", _StartUC),
            patch.object(h, "ConsentRepository", _ConsentRepo),
            patch.object(h, "cleanup_messages", AsyncMock()),
            patch.object(h, "answer_tracked", answer),
        ):
            await h.start_master_registration(
                message=message,
                state=state,
                user_ctx_storage=SimpleNamespace(),
                rate_limiter=None,
                admin_alerter=None,
                token=None,
            )

        self.assertEqual(state._state, h.MasterRegistration.consent)
        answer.assert_awaited()

    async def test_agree_saves_consent_and_advances_to_name(self) -> None:
        from src.handlers.master import register as h

        state = MemoryState()
        await state.set_state(h.MasterRegistration.consent)

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            message=SimpleNamespace(),
            bot=SimpleNamespace(),
            answer=AsyncMock(),
        )

        upsert = AsyncMock()

        class _ConsentRepo:
            def __init__(self, session) -> None:
                pass

            upsert_consent = upsert

        edit = AsyncMock(return_value=True)
        with (
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "ConsentRepository", _ConsentRepo),
            patch.object(h, "safe_edit_text", edit),
            patch.object(h, "track_callback_message", AsyncMock()),
        ):
            await h.master_reg_pd_agree(callback=callback, state=state)

        self.assertEqual(state._state, h.MasterRegistration.name)
        upsert.assert_awaited()
        edit.assert_awaited()

    async def test_decline_sets_declined_state(self) -> None:
        from src.handlers.master import register as h

        state = MemoryState()
        await state.set_state(h.MasterRegistration.consent)

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            message=SimpleNamespace(),
            bot=SimpleNamespace(),
            answer=AsyncMock(),
        )

        edit = AsyncMock(return_value=True)
        with (
            patch.object(h, "safe_edit_reply_markup", AsyncMock(return_value=True)),
            patch.object(h, "safe_edit_text", edit),
            patch.object(h, "track_callback_message", AsyncMock()),
        ):
            await h.master_reg_pd_decline(callback=callback, state=state)

        self.assertEqual(state._state, h.MasterRegistration.consent_declined)
        edit.assert_awaited()
