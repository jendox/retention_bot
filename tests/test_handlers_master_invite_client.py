from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


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
        from src.notifications.policy import DefaultNotificationPolicy
        from src.use_cases.create_master_client_invite import (
            CreateMasterClientInviteOutcome,
            CreateMasterClientInviteResult,
        )

        state = MemoryState()
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(delete_message=AsyncMock(), send_message=AsyncMock()),
            message=SimpleNamespace(message_id=1, chat=SimpleNamespace(id=10)),
            answer=AsyncMock(),
        )
        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True), policy=DefaultNotificationPolicy())

        class _UseCase:
            def __init__(self, session) -> None:
                pass

            async def execute(self, *, master_telegram_id: int):
                return CreateMasterClientInviteResult(
                    outcome=CreateMasterClientInviteOutcome.QUOTA_EXCEEDED,
                    plan=SimpleNamespace(is_pro=False),
                    usage=SimpleNamespace(clients_count=10, bookings_created_this_month=0),
                    clients_limit=10,
                )

        with (
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "CreateMasterClientInvite", _UseCase),
        ):
            await h.start_invite_client(callback=callback, state=state, notifier=notifier)

        self.assertIsNone(state._state)

    async def test_start_invite_client_success_sets_state(self) -> None:
        from src.handlers.master import invite_client as h
        from src.notifications.policy import DefaultNotificationPolicy
        from src.use_cases.create_master_client_invite import (
            CreateMasterClientInviteOutcome,
            CreateMasterClientInviteResult,
        )

        state = MemoryState()
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(send_message=AsyncMock(), delete_message=AsyncMock()),
            message=SimpleNamespace(message_id=1, chat=SimpleNamespace(id=10)),
            answer=AsyncMock(),
        )
        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True), policy=DefaultNotificationPolicy())

        class _UseCase:
            def __init__(self, session) -> None:
                pass

            async def execute(self, *, master_telegram_id: int):
                return CreateMasterClientInviteResult(
                    outcome=CreateMasterClientInviteOutcome.OK,
                    invite_link="https://t.me/x?start=c_t",
                    master_name="Master",
                    plan=SimpleNamespace(is_pro=False),
                    usage=SimpleNamespace(clients_count=0, bookings_created_this_month=0),
                    clients_limit=10,
                )

        answer_tracked = AsyncMock()
        with (
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "CreateMasterClientInvite", _UseCase),
            patch.object(h, "answer_tracked", answer_tracked),
        ):
            await h.start_invite_client(callback=callback, state=state, notifier=notifier)

        data = await state.get_data()
        self.assertEqual(data["invite_link"], "https://t.me/x?start=c_t")
        self.assertEqual(data["master_name"], "Master")
        self.assertEqual(state._state, h.MasterInviteClient.choosing_format)
        answer_tracked.assert_awaited()

    async def test_choose_format_escapes_user_content_in_message(self) -> None:
        from src.handlers.master import invite_client as h

        state = MemoryState()
        await state.update_data(invite_link='https://t.me/x?start="bad"', master_name="<b>M</b>")

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            data="m:invite:friendly",
            message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
            bot=SimpleNamespace(),
            answer=AsyncMock(),
        )

        with patch.object(h, "_reset_invite_flow", AsyncMock()):
            await h.master_invite_choose_format(callback=callback, state=state)

        callback.message.answer.assert_awaited()
        sent_text = callback.message.answer.await_args.args[0]
        self.assertIn("&lt;b&gt;M&lt;/b&gt;", sent_text)
        self.assertIn('href="https://t.me/x?start=&quot;bad&quot;"', sent_text)
