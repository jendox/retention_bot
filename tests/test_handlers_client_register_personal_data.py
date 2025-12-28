from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.use_cases.accept_client_invite import AcceptClientInviteResult, AcceptInviteOutcome


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
async def _fake_active_session():
    yield object()


class ClientRegistrationPersonalDataTests(unittest.IsolatedAsyncioTestCase):
    async def test_agree_saves_consent_and_completes_registration(self) -> None:
        from src.handlers.client import register as h

        state = MemoryState()
        await state.update_data(invite_token="token123")
        await state.set_state(h.ClientRegistration.consent)

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

        class _UC:
            def __init__(self, session) -> None:
                pass

            async def execute(self, request):  # noqa: ARG001
                return AcceptClientInviteResult(ok=True, outcome=AcceptInviteOutcome.CREATED, master_id=1, client_id=2)

        reset = AsyncMock()
        send_menu = AsyncMock()
        with (
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "ConsentRepository", _ConsentRepo),
            patch.object(h, "AcceptClientInvite", _UC),
            patch.object(h, "_reset_flow", reset),
            patch.object(h, "_send_menu_after_registration", send_menu),
            patch.object(h, "track_callback_message", AsyncMock()),
        ):
            await h.client_reg_pd_agree(
                callback=callback,
                state=state,
                user_ctx_storage=SimpleNamespace(),
            )

        upsert.assert_awaited()
        reset.assert_awaited()
        send_menu.assert_awaited()

    async def test_decline_sets_declined_state(self) -> None:
        from src.handlers.client import register as h

        state = MemoryState()
        await state.set_state(h.ClientRegistration.consent)

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            message=SimpleNamespace(),
            bot=SimpleNamespace(),
            answer=AsyncMock(),
        )

        edit = AsyncMock(return_value=True)
        with (
            patch.object(h, "safe_edit_text", edit),
            patch.object(h, "track_callback_message", AsyncMock()),
        ):
            await h.client_reg_pd_decline(callback=callback, state=state)

        self.assertEqual(state._state, h.ClientRegistration.consent_declined)
        edit.assert_awaited()
