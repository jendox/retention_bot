from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class MemoryState:
    def __init__(self) -> None:
        self._data: dict = {}
        self._state = None

    async def get_data(self) -> dict:
        return dict(self._data)

    async def update_data(self, **kwargs) -> None:
        self._data.update(kwargs)

    async def set_state(self, state) -> None:  # noqa: ANN001
        self._state = state

    async def clear(self) -> None:
        self._data = {}
        self._state = None


class MasterWorkdayOverridesCalendarTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_overrides_opens_month_calendar(self) -> None:
        from src.handlers.master import workday_overrides as h

        state = MemoryState()
        message = SimpleNamespace(
            chat=SimpleNamespace(id=10),
            message_id=1,
            edit_text=AsyncMock(),
        )
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            message=message,
            bot=SimpleNamespace(),
            answer=AsyncMock(),
        )

        fixed = date(2026, 1, 2)
        with (
            patch.object(h, "rate_limit_callback", AsyncMock(return_value=True)),
            patch.object(h, "cleanup_messages", AsyncMock()),
            patch.object(h, "_calendar_limits", lambda: h.month_calendar.CalendarLimits(  # noqa: E731
                today=fixed,
                min_date=fixed,
                max_date=fixed,
                pro_max_date=fixed,
                plan_is_pro=True,
            )),
            patch.object(h, "safe_edit_text", AsyncMock(return_value=True)),
        ):
            await h.start_overrides(callback=callback, state=state)

        self.assertEqual(state._state, h.WorkdayOverrideStates.picking_date)

    async def test_pick_override_day_calls_render(self) -> None:
        from src.handlers.master import workday_overrides as h

        state = MemoryState()
        await state.set_state(h.WorkdayOverrideStates.picking_date)

        message = SimpleNamespace(edit_text=AsyncMock())
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            message=message,
            bot=SimpleNamespace(),
            answer=AsyncMock(),
            data=f"{h.MONTH_CAL_PREFIX}:d:20260105",
        )

        render = AsyncMock()
        fixed = date(2026, 1, 2)
        with (
            patch.object(h, "rate_limit_callback", AsyncMock(return_value=True)),
            patch.object(h, "_calendar_limits", lambda: h.month_calendar.CalendarLimits(  # noqa: E731
                today=fixed,
                min_date=fixed,
                max_date=fixed,
                pro_max_date=fixed,
                plan_is_pro=True,
            )),
            patch.object(h, "_render_day_menu_main", render),
        ):
            await h.pick_override_day(callback=callback, state=state)

        render.assert_awaited()
