from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


@asynccontextmanager
async def _fake_session_local():
    yield object()


class _Master:
    def __init__(self, *, master_id: int, name: str) -> None:
        self.id = master_id
        self.name = name
        self.telegram_id = 999
        self.phone = "+375291234567"
        self.work_days = [0, 1, 2, 3, 4]
        self.start_time = "09:00"
        self.end_time = "18:00"

    def to_state_dict(self) -> dict[str, object]:
        return {
            "id": int(self.id),
            "telegram_id": int(self.telegram_id),
            "name": str(self.name),
            "phone": str(self.phone),
            "work_days": list(self.work_days),
            "start_time": str(self.start_time),
            "end_time": str(self.end_time),
        }


class _Booking:
    def __init__(self, *, master_id: int, master_name: str) -> None:
        self.id = 1
        self.start_at = datetime.now(UTC)
        self.duration_min = 60
        self.status = SimpleNamespace(value="CONFIRMED")
        self.master = SimpleNamespace(
            id=master_id,
            name=master_name,
            telegram_id=123,
            timezone=SimpleNamespace(value="Europe/Minsk"),
        )


class ClientMasterAliasTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_masters_load_applies_master_alias(self) -> None:
        from src.handlers.client import list_masters

        client = SimpleNamespace(id=1, masters=[_Master(master_id=10, name="Profile")])

        class _ClientRepo:
            async def get_details_by_telegram_id(self, telegram_id: int):  # noqa: ARG002
                return client

        class _MasterRepo:
            async def get_master_aliases_for_client(self, *, client_id: int) -> dict[int, str]:  # noqa: ARG002
                return {10: "Alias"}

        with (
            patch.object(list_masters, "session_local", _fake_session_local),
            patch.object(list_masters, "ClientRepository", lambda session: _ClientRepo()),  # noqa: ARG005
            patch.object(list_masters, "MasterClientRepository", lambda session: _MasterRepo()),  # noqa: ARG005
        ):
            masters = await list_masters._load_masters(telegram_id=777)

        assert masters is not None
        self.assertEqual(masters[0]["name"], "Alias")

    async def test_client_booking_master_keyboard_uses_alias(self) -> None:
        from src.handlers.client import booking as booking_h

        m1 = _Master(master_id=10, name="M1")
        m2 = _Master(master_id=11, name="M2")
        client = SimpleNamespace(id=1, name="Client", timezone=SimpleNamespace(value="Europe/Minsk"), masters=[m1, m2])

        class _ClientRepo:
            async def get_details_by_telegram_id(self, telegram_id: int):  # noqa: ARG002
                return client

        class _MasterRepo:
            async def get_master_aliases_for_client(self, *, client_id: int) -> dict[int, str]:  # noqa: ARG002
                return {10: "Alias 1"}

        captured = SimpleNamespace(markup=None)

        async def _answer_tracked(message, state, *, text, reply_markup, bucket, parse_mode=None):  # noqa: ARG001
            captured.markup = reply_markup

        message = SimpleNamespace(from_user=SimpleNamespace(id=777), bot=SimpleNamespace())
        state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())

        with (
            patch.object(booking_h, "rate_limit_message", AsyncMock(return_value=True)),
            patch.object(booking_h, "track_message", AsyncMock()),
            patch.object(booking_h, "answer_tracked", _answer_tracked),
            patch.object(booking_h, "session_local", _fake_session_local),
            patch.object(booking_h, "ClientRepository", lambda session: _ClientRepo()),  # noqa: ARG005
            patch.object(booking_h, "MasterClientRepository", lambda session: _MasterRepo()),  # noqa: ARG005
        ):
            await booking_h.start_client_add_booking(message=message, state=state)

        self.assertIsNotNone(captured.markup)
        first_button_text = captured.markup.inline_keyboard[0][0].text
        self.assertEqual(first_button_text, "Alias 1")
