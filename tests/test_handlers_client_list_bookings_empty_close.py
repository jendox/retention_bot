from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class ClientListBookingsEmptyCloseTests(unittest.IsolatedAsyncioTestCase):
    async def test_render_select_empty_keeps_close_button_and_main_ref(self) -> None:
        from src.handlers.client import list_bookings as h
        from src.schemas.enums import Timezone

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            message=SimpleNamespace(chat=SimpleNamespace(id=10), message_id=55),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(update_data=AsyncMock())

        mock_safe_edit_text = AsyncMock(return_value=True)
        with (
            patch.object(h, "_fetch_client_bookings", AsyncMock(return_value=(Timezone("Europe/Minsk"), []))),
            patch.object(h, "safe_edit_text", mock_safe_edit_text),
        ):
            await h._render_select(callback=callback, state=state, page=1, chunk=1)

        _kwargs = mock_safe_edit_text.await_args.kwargs
        markup = _kwargs.get("reply_markup")
        self.assertIsNotNone(markup)
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, f"{h.CB_PREFIX}close")

        state.update_data.assert_awaited_once()
        saved = state.update_data.await_args.kwargs
        self.assertEqual(saved[h.LIST_BOOKINGS_MAIN_KEY]["chat_id"], 10)
        self.assertEqual(saved[h.LIST_BOOKINGS_MAIN_KEY]["message_id"], 55)
