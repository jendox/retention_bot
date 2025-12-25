from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
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


@asynccontextmanager
async def _fake_session_local():
    yield object()


class ClientBookingHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_calendar_context_accepts_string_timezone(self) -> None:
        from src.handlers.client import booking as h

        state = MemoryState()
        await state.update_data(
            master_id=1,
            client_timezone="Europe/Minsk",
        )

        ctx = await h._load_calendar_context(state)
        self.assertIsNotNone(ctx)
        master_id, tz = ctx
        self.assertEqual(master_id, 1)
        self.assertEqual(str(tz.value), "Europe/Minsk")

    async def test_slot_not_available_recovers_to_slot_selection_when_slots_exist(self) -> None:
        from src.handlers.client import booking as h
        from src.schemas.enums import Timezone

        state = MemoryState()
        await state.update_data(
            master_id=1,
            client_timezone=Timezone("Europe/Minsk"),
            client_day="2025-12-31",
        )

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            message=SimpleNamespace(),
            answer=AsyncMock(),
        )

        slot1 = datetime(2025, 12, 31, 7, 0, tzinfo=UTC)
        free = SimpleNamespace(slots_utc=[slot1], slots_for_client=[slot1])

        safe_edit = AsyncMock()
        with (
            patch.object(h, "session_local", _fake_session_local),
            patch.object(h, "_get_free_slots", AsyncMock(return_value=free)),
            patch.object(h, "safe_edit_text", safe_edit),
        ):
            await h._recover_after_slot_not_available(callback=callback, state=state)

        callback.answer.assert_awaited()
        safe_edit.assert_awaited()
        data = await state.get_data()
        self.assertEqual(data["booking_slots_utc"], [slot1.isoformat()])
        self.assertIsNone(data["selected_slot_index"])
        self.assertEqual(state._state, h.ClientBooking.selecting_slot)

    async def test_slot_not_available_recovers_to_calendar_when_no_slots(self) -> None:
        from src.handlers.client import booking as h
        from src.schemas.enums import Timezone

        state = MemoryState()
        await state.update_data(
            master_id=1,
            client_timezone=Timezone("Europe/Minsk"),
            client_day=date(2025, 12, 31).isoformat(),
        )

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            message=SimpleNamespace(),
            answer=AsyncMock(),
        )

        free = SimpleNamespace(slots_utc=[], slots_for_client=[])

        class _Calendar:
            async def start_calendar(self):
                return SimpleNamespace()

        safe_edit = AsyncMock()
        with (
            patch.object(h, "session_local", _fake_session_local),
            patch.object(h, "_get_free_slots", AsyncMock(return_value=free)),
            patch.object(h, "SimpleCalendar", _Calendar),
            patch.object(h, "safe_edit_text", safe_edit),
        ):
            await h._recover_after_slot_not_available(callback=callback, state=state)

        callback.answer.assert_awaited()
        safe_edit.assert_awaited()
        self.assertEqual(state._state, h.ClientBooking.selecting_date)
