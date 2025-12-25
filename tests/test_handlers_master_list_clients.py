from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class MasterListClientsHandlerTests(unittest.IsolatedAsyncioTestCase):
    def test_build_clients_page_text_escapes_name_and_marks_offline(self) -> None:
        from src.handlers.master import list_clients as h

        client = SimpleNamespace(name="<b>X</b>", phone="+375291234567", telegram_id=None)
        text = h._build_clients_page_text([client], page=1, total_pages=1, start_index=0)
        self.assertIn("&lt;b&gt;X&lt;/b&gt;", text)
        self.assertNotIn("<b>", text)
        self.assertIn("🔴", text)

    async def test_pagination_parses_page_and_uses_global_index(self) -> None:
        from src.handlers.master import list_clients as h

        clients = [SimpleNamespace(name=f"C{i}", phone=None, telegram_id=1) for i in range(1, 16)]

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            data=f"{h.CLIENTS_PAGE_PREFIX}2",
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock(), edit_reply_markup=AsyncMock()),
        )

        with patch.object(h, "_fetch_master_clients", AsyncMock(return_value=clients)):
            await h.master_clients_pagination(callback)

        callback.message.edit_text.assert_awaited()
        sent_text = callback.message.edit_text.await_args.kwargs["text"]
        self.assertIn("11. C11", sent_text)

    async def test_close_falls_back_to_hiding_keyboard_on_delete_race(self) -> None:
        from src.handlers.master import list_clients as h

        class _BadRequest(Exception):
            pass

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            data=f"{h.CLIENTS_PAGE_PREFIX}close",
            answer=AsyncMock(),
            message=SimpleNamespace(
                delete=AsyncMock(side_effect=_BadRequest("message to delete not found")),
                edit_reply_markup=AsyncMock(),
            ),
        )

        with patch.object(h, "TelegramBadRequest", _BadRequest):
            await h.master_clients_pagination(callback)

        callback.message.edit_reply_markup.assert_awaited_with(reply_markup=None)
