from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import InlineKeyboardMarkup


class MemoryState:
    def __init__(self) -> None:
        self._data: dict = {}
        self._state = None

    async def get_data(self) -> dict:
        return dict(self._data)

    async def set_state(self, state) -> None:  # noqa: ANN001
        self._state = state

    async def update_data(self, **kwargs) -> None:
        self._data.update(kwargs)

    async def clear(self) -> None:
        self._data = {}
        self._state = None


class MasterEditClientBookCalendarTests(unittest.IsolatedAsyncioTestCase):
    async def test_book_client_opens_month_calendar(self) -> None:
        from src.handlers.master import add_booking as add_booking_h, edit_client as h

        state = MemoryState()
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            message=SimpleNamespace(),
            answer=AsyncMock(),
            data="m:edit_client:book:2",
            bot=SimpleNamespace(),
        )

        class _Client:
            id = 2

            def to_state_dict(self) -> dict:
                return {"id": 2}

        master = SimpleNamespace(
            id=1,
            slot_size_min=60,
            timezone=SimpleNamespace(value="Europe/Minsk"),
            clients=[_Client()],
        )

        @asynccontextmanager
        async def _session_local():
            yield object()

        class _MasterRepo:
            def __init__(self, _session) -> None:  # noqa: ANN001
                _ = _session

            async def get_with_clients_by_telegram_id(self, _telegram_id: int):  # noqa: ANN201
                _ = _telegram_id
                return master

        markup = InlineKeyboardMarkup(inline_keyboard=[])
        with (
            patch.object(h, "session_local", _session_local),
            patch.object(h, "MasterRepository", _MasterRepo),
            patch.object(add_booking_h, "_calendar_markup", AsyncMock(return_value=markup)),
            patch.object(h, "safe_edit_text", AsyncMock(return_value=True)),
        ):
            await h.book_client(callback=callback, state=state)

        self.assertEqual(state._state, add_booking_h.AddBookingStates.selecting_date)
