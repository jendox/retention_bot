from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.use_cases.create_master_booking import CreateMasterBookingError, CreateMasterBookingResult
from src.use_cases.entitlements import Usage


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

@asynccontextmanager
async def _fake_session_local():
    yield object()


class MasterAddBookingHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_pick_date_no_slots_keeps_calendar_and_answers(self) -> None:
        from src.handlers.master import add_booking as h

        state = MemoryState()
        await state.update_data(
            master_id=1,
            master_timezone="Europe/Minsk",
        )

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        callback_data = SimpleNamespace()

        class _Calendar:
            async def process_selection(self, callback, callback_data):
                return True, datetime.now(UTC)

            async def start_calendar(self):
                return SimpleNamespace()

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

            async def max_booking_horizon_days(self, *, master_id: int):
                return 30

        class _Slots:
            def __init__(self, session) -> None:
                pass

            async def execute(self, *, master_id, client_day, client_tz):
                return SimpleNamespace(slots_utc=[], master_day=client_day)

        with (
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "SimpleCalendar", _Calendar),
            patch.object(h, "session_local", _fake_session_local),
            patch.object(h, "EntitlementsService", _Entitlements),
            patch.object(h, "GetMasterFreeSlots", _Slots),
        ):
            await h.pick_date(callback=callback, callback_data=callback_data, state=state)

        callback.answer.assert_awaited()
        callback.message.edit_text.assert_awaited()

    async def test_pick_date_out_of_range_restores_calendar(self) -> None:
        from src.handlers.master import add_booking as h

        state = MemoryState()
        await state.update_data(
            master_id=1,
            master_timezone="Europe/Minsk",
        )

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        callback_data = SimpleNamespace()

        class _Calendar:
            async def process_selection(self, callback, callback_data):
                return True, datetime.now(UTC)

            async def start_calendar(self):
                return SimpleNamespace()

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

            async def max_booking_horizon_days(self, *, master_id: int):
                return 0  # only today allowed

        class _Slots:
            def __init__(self, session) -> None:
                pass

            async def execute(self, *, master_id, client_day, client_tz):
                return SimpleNamespace(slots_utc=[], master_day=client_day)

        with (
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "SimpleCalendar", _Calendar),
            patch.object(h, "session_local", _fake_session_local),
            patch.object(h, "EntitlementsService", _Entitlements),
            patch.object(h, "GetMasterFreeSlots", _Slots),
        ):
            await h.pick_date(callback=callback, callback_data=callback_data, state=state)

        callback.answer.assert_awaited()
        callback.message.edit_text.assert_awaited()

    async def test_confirm_quota_exceeded_resets_state(self) -> None:
        from src.handlers.master import add_booking as h

        state = MemoryState()
        slot = datetime.now(UTC) + timedelta(days=1)
        await state.update_data(
            confirm_in_progress=False,
            selected_slot=slot.isoformat(),
            client={
                "id": 2,
                "telegram_id": None,
                "name": "N",
                "timezone": "Europe/Minsk",
                "notifications_enabled": True,
            },
            master_id=1,
            master_timezone="Europe/Minsk",
            slots=[slot.isoformat()],
            master_day=slot.date().isoformat(),
        )

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(edit_reply_markup=AsyncMock(), edit_text=AsyncMock(), answer=AsyncMock()),
            answer=AsyncMock(),
        )

        class _UC:
            def __init__(self, session) -> None:
                pass

            async def execute(self, request):
                return CreateMasterBookingResult(
                    ok=False,
                    error=CreateMasterBookingError.QUOTA_EXCEEDED,
                    plan_is_pro=False,
                    bookings_limit=10,
                    usage=Usage(clients_count=0, bookings_created_this_month=10),
                )

        cleanup = AsyncMock()
        maybe_send = AsyncMock(return_value=False)
        with (
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "CreateMasterBooking", _UC),
            patch.object(h, "cleanup_messages", cleanup),
        ):
            await h.confirm_booking(
                callback=callback,
                state=state,
                notifier=SimpleNamespace(maybe_send=maybe_send),
            )

        cleanup.assert_awaited()
        maybe_send.assert_awaited()
        self.assertEqual(await state.get_data(), {})

    async def test_confirm_slot_taken_returns_to_slot_selection(self) -> None:
        from src.handlers.master import add_booking as h

        state = MemoryState()
        slot = datetime.now(UTC) + timedelta(days=1)
        await state.update_data(
            confirm_in_progress=False,
            selected_slot=slot.isoformat(),
            client={
                "id": 2,
                "telegram_id": None,
                "name": "N",
                "timezone": "Europe/Minsk",
                "notifications_enabled": True,
            },
            master_id=1,
            master_timezone="Europe/Minsk",
            slots=[slot.isoformat()],
            master_day=slot.date().isoformat(),
        )

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(edit_reply_markup=AsyncMock(), edit_text=AsyncMock(), answer=AsyncMock()),
            answer=AsyncMock(),
        )

        class _UC:
            def __init__(self, session) -> None:
                pass

            async def execute(self, request):
                return CreateMasterBookingResult(ok=False, error=CreateMasterBookingError.SLOT_NOT_AVAILABLE)

        with (
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "CreateMasterBooking", _UC),
        ):
            await h.confirm_booking(
                callback=callback,
                state=state,
                notifier=SimpleNamespace(maybe_send=AsyncMock()),
            )

        self.assertEqual(state._state, h.AddBookingStates.selecting_slot)
        callback.message.edit_text.assert_awaited()
        data = await state.get_data()
        self.assertFalse(bool(data.get("confirm_in_progress")))
