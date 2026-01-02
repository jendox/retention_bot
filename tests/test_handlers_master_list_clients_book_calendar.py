from __future__ import annotations

import unittest
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


class MasterListClientsBookCalendarTests(unittest.IsolatedAsyncioTestCase):
    async def test_book_from_client_card_opens_month_calendar(self) -> None:
        from src.handlers.master import add_booking as add_booking_h, list_clients as h

        state = MemoryState()
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            message=SimpleNamespace(),
            answer=AsyncMock(),
            data=f"{h.CLIENTS_CARD_PREFIX}book:ignored",
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

        markup = InlineKeyboardMarkup(inline_keyboard=[])
        with (
            patch.object(h, "_parse_card_action", lambda *_args, **_kwargs: (2, 1, 1)),  # noqa: E731
            patch.object(h, "_fetch_master_or_alert", AsyncMock(return_value=master)),
            patch.object(add_booking_h, "_calendar_markup", AsyncMock(return_value=markup)),
            patch.object(h, "safe_edit_text", AsyncMock(return_value=True)),
        ):
            await h.master_clients_card_book(callback=callback, state=state)

        self.assertEqual(state._state, add_booking_h.AddBookingStates.selecting_date)
