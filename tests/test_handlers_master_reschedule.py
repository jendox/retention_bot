from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
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


@asynccontextmanager
async def _fake_active_session():
    yield object()


class MasterRescheduleHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_pick_date_no_slots_restores_calendar(self) -> None:
        from src.handlers.master import reschedule as h

        state = MemoryState()
        await state.update_data(
            reschedule_booking_id=7,
            reschedule_master_id=1,
            reschedule_master_tz="Europe/Minsk",
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

            async def execute(self, *, master_id, client_day, client_tz, exclude_booking_id=None):
                return SimpleNamespace(slots_utc=[], slots_for_client=[], master_day=client_day)

        with (
            patch.object(h, "SimpleCalendar", _Calendar),
            patch.object(h, "session_local", _fake_session_local),
            patch.object(h, "EntitlementsService", _Entitlements),
            patch.object(h, "GetMasterFreeSlots", _Slots),
            patch.object(h, "cleanup_messages", AsyncMock()),
        ):
            await h.pick_date(callback=callback, callback_data=callback_data, state=state)

        callback.answer.assert_awaited()
        callback.message.edit_text.assert_awaited()

    async def test_confirm_slot_taken_returns_to_calendar(self) -> None:
        from src.handlers.master import reschedule as h

        state = MemoryState()
        slot = datetime.now(UTC) + timedelta(days=1)
        await state.update_data(
            confirm_in_progress=False,
            reschedule_booking_id=7,
            reschedule_selected_slot=slot.isoformat(),
            reschedule_master_tz="Europe/Minsk",
            reschedule_client_tg=123,
            reschedule_scope="TODAY",
            reschedule_page=1,
        )

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(edit_reply_markup=AsyncMock(), edit_text=AsyncMock()),
            answer=AsyncMock(),
        )

        class _UC:
            def __init__(self, session) -> None:
                pass

            async def execute(self, request):
                return SimpleNamespace(
                    ok=False,
                    error=h.RescheduleMasterBookingError.SLOT_NOT_AVAILABLE,
                    plan_is_pro=True,
                )

        class _Calendar:
            async def start_calendar(self):
                return SimpleNamespace()

        with (
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "RescheduleMasterBooking", _UC),
            patch.object(h, "SimpleCalendar", _Calendar),
            patch.object(h, "cleanup_messages", AsyncMock()),
        ):
            await h.confirm(
                callback=callback,
                state=state,
                notifier=SimpleNamespace(maybe_send=AsyncMock()),
            )

        callback.answer.assert_awaited()
        callback.message.edit_text.assert_awaited()

    async def test_pick_date_hides_current_booking_slot(self) -> None:
        from src.handlers.master import reschedule as h

        state = MemoryState()
        base = datetime.now(UTC) + timedelta(days=1)
        original_slot = base.replace(hour=10, minute=30, second=0, microsecond=0)
        other_slot = base.replace(hour=11, minute=0, second=0, microsecond=0)
        await state.update_data(
            reschedule_booking_id=7,
            reschedule_master_id=1,
            reschedule_master_tz="Europe/Minsk",
            reschedule_original_start_at=original_slot.isoformat(),
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
                return True, base

            async def start_calendar(self):
                return SimpleNamespace()

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

            async def max_booking_horizon_days(self, *, master_id: int):
                return 365

        class _Slots:
            def __init__(self, session) -> None:
                pass

            async def execute(self, *, master_id, client_day, client_tz, exclude_booking_id=None):
                return SimpleNamespace(
                    slots_utc=[original_slot, other_slot],
                    slots_for_client=[original_slot, other_slot],
                    master_day=client_day,
                )

        with (
            patch.object(h, "SimpleCalendar", _Calendar),
            patch.object(h, "session_local", _fake_session_local),
            patch.object(h, "EntitlementsService", _Entitlements),
            patch.object(h, "GetMasterFreeSlots", _Slots),
            patch.object(h, "cleanup_messages", AsyncMock()),
        ):
            await h.pick_date(callback=callback, callback_data=callback_data, state=state)

        data = await state.get_data()
        self.assertEqual(data["reschedule_slots"], [other_slot.isoformat()])
