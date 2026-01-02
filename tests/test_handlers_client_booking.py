from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
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

        safe_edit = AsyncMock()
        limits = h.month_calendar.CalendarLimits(
            today=date(2025, 12, 30),
            min_date=date(2025, 12, 31),
            max_date=date(2026, 1, 6),
            pro_max_date=date(2026, 2, 28),
            plan_is_pro=False,
        )
        with (
            patch.object(h, "session_local", _fake_session_local),
            patch.object(h, "_get_free_slots", AsyncMock(return_value=free)),
            patch.object(h, "_calendar_limits", AsyncMock(return_value=limits)),
            patch.object(h, "safe_edit_text", safe_edit),
        ):
            await h._recover_after_slot_not_available(callback=callback, state=state)

        callback.answer.assert_awaited()
        safe_edit.assert_awaited()
        self.assertEqual(state._state, h.ClientBooking.selecting_date)

    async def test_back_to_calendar_resets_to_date_selection(self) -> None:
        from src.handlers.client import booking as h

        state = MemoryState()
        await state.update_data(
            client_day="2025-12-31",
            booking_slots_utc=["2025-12-31T07:00:00+00:00"],
            selected_slot_index=0,
        )

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            message=SimpleNamespace(),
            answer=AsyncMock(),
        )

        safe_edit = AsyncMock()
        limits = h.month_calendar.CalendarLimits(
            today=date.today(),
            min_date=date.today() + timedelta(days=1),
            max_date=date.today() + timedelta(days=7),
            pro_max_date=date.today() + timedelta(days=60),
            plan_is_pro=False,
        )
        with (
            patch.object(h, "_calendar_limits", AsyncMock(return_value=limits)),
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "safe_edit_text", safe_edit),
        ):
            await h.booking_back_to_calendar(callback=callback, state=state)

        callback.answer.assert_awaited()
        safe_edit.assert_awaited()
        data = await state.get_data()
        self.assertEqual(data["booking_slots_utc"], [])
        self.assertIsNone(data["selected_slot_index"])
        self.assertIsNone(data["client_day"])
        self.assertEqual(state._state, h.ClientBooking.selecting_date)

    async def test_back_to_slots_restores_slot_selection(self) -> None:
        from src.handlers.client import booking as h
        from src.schemas.enums import Timezone

        state = MemoryState()
        await state.update_data(
            client_day="2025-12-31",
            booking_slots_utc=["2025-12-31T07:00:00+00:00"],
            selected_slot_index=0,
            client_timezone=Timezone("Europe/Minsk"),
        )

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            message=SimpleNamespace(),
            answer=AsyncMock(),
        )

        safe_edit = AsyncMock()
        with (
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "safe_edit_text", safe_edit),
        ):
            await h.booking_back_to_slots(callback=callback, state=state)

        callback.answer.assert_awaited()
        safe_edit.assert_awaited()
        data = await state.get_data()
        self.assertIsNone(data["selected_slot_index"])
        self.assertEqual(state._state, h.ClientBooking.selecting_slot)

    async def test_cancel_does_not_send_chat_message(self) -> None:
        from src.handlers.client import booking as h

        state = MemoryState()
        callback_message = SimpleNamespace(answer=AsyncMock())
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            message=callback_message,
            bot=SimpleNamespace(),
            answer=AsyncMock(),
        )

        with patch.object(h, "cleanup_messages", AsyncMock()):
            await h.booking_cancel(callback=callback, state=state)

        callback.answer.assert_awaited()
        callback_message.answer.assert_not_awaited()

    async def test_cancel_flow_does_not_send_chat_message(self) -> None:
        from src.handlers.client import booking as h

        state = MemoryState()
        callback_message = SimpleNamespace(answer=AsyncMock())
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            message=callback_message,
            bot=SimpleNamespace(),
            answer=AsyncMock(),
        )

        with patch.object(h, "cleanup_messages", AsyncMock()):
            await h.booking_cancel_flow(callback=callback, state=state)

        callback.answer.assert_awaited()
        callback_message.answer.assert_not_awaited()

    async def test_locked_date_click_shows_toast(self) -> None:
        from src.handlers.client import booking as h

        state = MemoryState()
        await state.update_data(master_id=1, client_timezone="Europe/Minsk", booking_calendar_month="202601")
        await state.set_state(h.ClientBooking.selecting_date)

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            data=f"{h.MONTH_CAL_PREFIX}:l:20260110",
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )

        limits = h.month_calendar.CalendarLimits(
            today=date(2026, 1, 2),
            min_date=date(2026, 1, 3),
            max_date=date(2026, 1, 9),
            pro_max_date=date(2026, 3, 2),
            plan_is_pro=False,
        )

        with (
            patch.object(h, "_calendar_limits", AsyncMock(return_value=limits)),
            patch.object(h, "rate_limit_callback", AsyncMock(return_value=True)),
            patch.object(h, "track_callback_message", AsyncMock()),
        ):
            await h.process_booking_calendar_month(callback=callback, state=state)

        callback.answer.assert_awaited()
        callback.message.edit_text.assert_not_awaited()
