from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.use_cases.create_client_invite import CreateClientInviteResult


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


class MasterInviteClientHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_invite_client_quota_exceeded(self) -> None:
        from src.handlers.master import invite_client as h

        state = MemoryState()
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(),
            message=SimpleNamespace(message_id=1, chat=SimpleNamespace(id=10)),
            answer=AsyncMock(),
        )

        class _MasterRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(id=1, telegram_id=telegram_id, name="M")

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

            async def can_attach_client(self, master_id: int):
                return SimpleNamespace(allowed=False, current=10, limit=10)

        answer_tracked = AsyncMock()
        with (
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "MasterRepository", _MasterRepo),
            patch.object(h, "EntitlementsService", _Entitlements),
            patch.object(h, "answer_tracked", answer_tracked),
        ):
            await h.start_invite_client(callback=callback, state=state)

        answer_tracked.assert_awaited()
        self.assertIsNone(state._state)

    async def test_start_invite_client_success_sets_state(self) -> None:
        from src.handlers.master import invite_client as h

        state = MemoryState()
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(message_id=1, chat=SimpleNamespace(id=10)),
            answer=AsyncMock(),
        )

        class _MasterRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(id=1, telegram_id=telegram_id, name="Master")

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

            async def can_attach_client(self, master_id: int):
                return SimpleNamespace(allowed=True, current=0, limit=10)

        class _CreateInvite:
            def __init__(self, session) -> None:
                pass

            async def execute_for_telegram(self, *, master_telegram_id: int) -> CreateClientInviteResult:
                return CreateClientInviteResult(
                    token="t",
                    link="https://t.me/x?start=c_t",
                    master_id=1,
                    master_name="Master",
                )

        answer_tracked = AsyncMock()
        with (
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "MasterRepository", _MasterRepo),
            patch.object(h, "EntitlementsService", _Entitlements),
            patch.object(h, "CreateClientInvite", _CreateInvite),
            patch.object(h, "answer_tracked", answer_tracked),
        ):
            await h.start_invite_client(callback=callback, state=state)

        data = await state.get_data()
        self.assertEqual(data["invite_link"], "https://t.me/x?start=c_t")
        self.assertEqual(data["master_name"], "Master")
        self.assertEqual(state._state, h.MasterInviteClient.choosing_format)
        answer_tracked.assert_awaited()
