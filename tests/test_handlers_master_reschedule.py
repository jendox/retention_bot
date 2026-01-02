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

        picked_day = datetime.now(UTC).date()
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            data=f"{h.MONTH_CAL_PREFIX}:d:{picked_day:%Y%m%d}",
        )
        limits = h.month_calendar.CalendarLimits(
            today=picked_day,
            min_date=picked_day,
            max_date=picked_day + timedelta(days=30),
            pro_max_date=picked_day + timedelta(days=60),
            plan_is_pro=True,
        )

        with (
            patch.object(h, "rate_limit_callback", AsyncMock(return_value=True)),
            patch.object(h, "_calendar_limits", AsyncMock(return_value=limits)),
            patch.object(
                h,
                "_validate_picked_day_in_horizon",
                AsyncMock(return_value=(True, picked_day, picked_day + timedelta(days=30))),
            ),
            patch.object(
                h,
                "_fetch_free_slots_for_day",
                AsyncMock(return_value=SimpleNamespace(slots_utc=[], slots_for_client=[], master_day=picked_day)),
            ),
            patch.object(h, "safe_edit_text", AsyncMock(return_value=True)),
            patch.object(h, "cleanup_messages", AsyncMock()),
        ):
            await h.pick_date(callback=callback, state=state)

        callback.answer.assert_awaited()

    async def test_confirm_slot_taken_returns_to_calendar(self) -> None:
        from src.handlers.master import reschedule as h

        state = MemoryState()
        slot = datetime.now(UTC) + timedelta(days=1)
        await state.update_data(
            confirm_in_progress=False,
            reschedule_booking_id=7,
            reschedule_selected_slot=slot.isoformat(),
            reschedule_master_id=1,
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

        limits = h.month_calendar.CalendarLimits(
            today=date.today(),
            min_date=date.today(),
            max_date=date.today() + timedelta(days=60),
            pro_max_date=date.today() + timedelta(days=60),
            plan_is_pro=True,
        )
        with (
            patch.object(h, "rate_limit_callback", AsyncMock(return_value=True)),
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "RescheduleMasterBooking", _UC),
            patch.object(h, "_calendar_limits", AsyncMock(return_value=limits)),
            patch.object(h, "safe_edit_text", AsyncMock(return_value=True)),
            patch.object(h, "cleanup_messages", AsyncMock()),
        ):
            await h.confirm(
                callback=callback,
                state=state,
                notifier=SimpleNamespace(maybe_send=AsyncMock()),
            )

        callback.answer.assert_awaited()

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
            data=f"{h.MONTH_CAL_PREFIX}:d:{original_slot.date():%Y%m%d}",
        )
        picked_day = original_slot.date()
        limits = h.month_calendar.CalendarLimits(
            today=picked_day,
            min_date=picked_day,
            max_date=picked_day + timedelta(days=365),
            pro_max_date=picked_day + timedelta(days=365),
            plan_is_pro=True,
        )

        with (
            patch.object(h, "rate_limit_callback", AsyncMock(return_value=True)),
            patch.object(h, "_calendar_limits", AsyncMock(return_value=limits)),
            patch.object(
                h,
                "_validate_picked_day_in_horizon",
                AsyncMock(return_value=(True, picked_day, picked_day + timedelta(days=365))),
            ),
            patch.object(
                h,
                "_fetch_free_slots_for_day",
                AsyncMock(
                    return_value=SimpleNamespace(
                        slots_utc=[original_slot, other_slot],
                        slots_for_client=[original_slot, other_slot],
                        master_day=picked_day,
                    ),
                ),
            ),
            patch.object(h, "safe_edit_text", AsyncMock(return_value=True)),
            patch.object(h, "cleanup_messages", AsyncMock()),
        ):
            await h.pick_date(callback=callback, state=state)

        data = await state.get_data()
        self.assertEqual(data["reschedule_slots"], [other_slot.isoformat()])
