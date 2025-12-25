from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.use_cases.create_client_offline import (
    CreateClientOfflineCreateResult,
    CreateClientOfflineError,
    CreateClientOfflinePreflightResult,
)


class MemoryState:
    def __init__(self) -> None:
        self._data: dict = {}
        self._state = None

    async def get_data(self) -> dict:
        return dict(self._data)

    async def set_data(self, data: dict) -> None:
        self._data = dict(data)

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


class MasterAddClientHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_add_client_happy_path_starts_fsm(self) -> None:
        from src.handlers.master import add_client as h

        state = MemoryState()
        bot = SimpleNamespace()
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=bot,
            message=SimpleNamespace(message_id=1, chat=SimpleNamespace(id=10)),
        )

        class _UC:
            def __init__(self, session) -> None:
                pass

            async def preflight(self, telegram_master_id: int) -> CreateClientOfflinePreflightResult:
                return CreateClientOfflinePreflightResult(
                    ok=True,
                    allowed=True,
                    master_id=1,
                    plan_is_pro=False,
                    clients_limit=10,
                )

        answer = AsyncMock()
        with (
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "CreateClientOffline", _UC),
            patch.object(h, "answer_tracked", answer),
        ):
            await h.start_add_client(callback=callback, state=state, notifier=SimpleNamespace(maybe_send=AsyncMock()))

        self.assertEqual(state._state, h.AddClientStates.name)
        answer.assert_awaited()

    async def test_confirm_phone_conflict_sets_state_back_to_phone(self) -> None:
        from src.handlers.master import add_client as h

        state = MemoryState()
        await state.update_data(name="N", phone="+375291234567")

        bot = SimpleNamespace(send_message=AsyncMock())
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=bot,
            message=SimpleNamespace(edit_reply_markup=AsyncMock(), answer=AsyncMock()),
            answer=AsyncMock(),
        )

        class _UC:
            def __init__(self, session) -> None:
                pass

            async def create(
                self,
                telegram_master_id: int,
                phone_e164: str,
                name: str,
            ) -> CreateClientOfflineCreateResult:
                return CreateClientOfflineCreateResult(ok=False, error=CreateClientOfflineError.PHONE_CONFLICT)

        answer = AsyncMock()
        with (
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "CreateClientOffline", _UC),
            patch.object(h, "answer_tracked", answer),
            patch.object(h, "cleanup_messages", AsyncMock()),
        ):
            await h.master_add_client_confirm(
                callback=callback,
                state=state,
                notifier=SimpleNamespace(maybe_send=AsyncMock()),
            )

        self.assertEqual(state._state, h.AddClientStates.phone)
        answer.assert_awaited()

    async def test_confirm_missing_data_clears_state(self) -> None:
        from src.handlers.master import add_client as h

        state = MemoryState()
        await state.update_data(name="N")  # missing phone

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(edit_reply_markup=AsyncMock(), answer=AsyncMock()),
            answer=AsyncMock(),
        )

        cleanup = AsyncMock()
        with (
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "cleanup_messages", cleanup),
        ):
            await h.master_add_client_confirm(
                callback=callback,
                state=state,
                notifier=SimpleNamespace(maybe_send=AsyncMock()),
            )

        cleanup.assert_awaited()
        data = await state.get_data()
        self.assertEqual(data, {})
