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


class ClientListMastersBookCalendarTests(unittest.IsolatedAsyncioTestCase):
    async def test_book_from_card_opens_month_calendar(self) -> None:
        from src.handlers.client import booking as booking_h, list_masters as h

        state = MemoryState()
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            message=SimpleNamespace(),
            answer=AsyncMock(),
            data=f"{h.CB_PREFIX}book:1",
            bot=SimpleNamespace(),
        )

        client = SimpleNamespace(
            id=2,
            timezone=SimpleNamespace(value="Europe/Minsk"),
            name="Анна",
        )

        @asynccontextmanager
        async def _session_local():
            yield object()

        class _ClientRepo:
            def __init__(self, _session) -> None:  # noqa: ANN001
                _ = _session

            async def get_details_by_telegram_id(self, _telegram_id: int):  # noqa: ANN201
                _ = _telegram_id
                return client

        class _Entitlements:
            def __init__(self, _session) -> None:  # noqa: ANN001
                _ = _session

            async def can_create_booking(self, *, master_id: int):  # noqa: ANN201
                _ = master_id
                return SimpleNamespace(allowed=True)

        markup = InlineKeyboardMarkup(inline_keyboard=[])
        with (
            patch.object(h, "rate_limit_callback", AsyncMock(return_value=True)),
            patch.object(h, "session_local", _session_local),
            patch.object(h, "ClientRepository", _ClientRepo),
            patch.object(h, "EntitlementsService", _Entitlements),
            patch.object(booking_h, "_calendar_markup", AsyncMock(return_value=markup)),
            patch.object(h, "safe_edit_text", AsyncMock(return_value=True)),
            patch.object(h, "track_message", AsyncMock(return_value=True)),
        ):
            await h.book_from_card(callback=callback, state=state, rate_limiter=None)

        from src.handlers.client.booking import ClientBooking

        self.assertEqual(state._state, ClientBooking.selecting_date)
