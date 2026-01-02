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
        self.assertIn("📵", text)
        self.assertIn("+37529*****67", text)
        self.assertNotIn("+375291234567", text)

    def test_no_placeholder_nav_buttons_when_single_page(self) -> None:
        from src.handlers.master import list_clients as h

        keyboard = h._build_list_menu_keyboard(page=1, total_pages=1)
        buttons = [btn for row in keyboard.inline_keyboard for btn in row]
        self.assertNotIn("m:noop", {btn.callback_data for btn in buttons})

    async def test_pagination_parses_page_and_uses_global_index(self) -> None:
        from src.handlers.master import list_clients as h

        clients = [SimpleNamespace(name=f"C{i}", phone=None, telegram_id=1) for i in range(1, 16)]
        master = SimpleNamespace(id=1, clients=clients)

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            data=f"{h.CLIENTS_CB_PREFIX}l:p:2",
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock(), edit_reply_markup=AsyncMock()),
        )

        with patch.object(h, "_fetch_master_with_clients", AsyncMock(return_value=master)):
            await h.master_clients_list_page(callback)

        callback.message.edit_text.assert_awaited()
        sent_text = callback.message.edit_text.await_args.kwargs["text"]
        self.assertIn("11. C11", sent_text)

    async def test_close_falls_back_to_hiding_keyboard_on_delete_race(self) -> None:
        from src.handlers.master import list_clients as h

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            data=f"{h.CLIENTS_PAGE_PREFIX}close",
            answer=AsyncMock(),
            message=SimpleNamespace(
                delete=AsyncMock(),
                edit_reply_markup=AsyncMock(),
            ),
        )

        with patch.object(h, "safe_delete", AsyncMock(return_value=False)):
            await h.master_clients_pagination(callback)

        callback.message.edit_reply_markup.assert_awaited_with(reply_markup=None)

    def test_client_card_shows_write_button_only_for_online(self) -> None:
        from src.handlers.master import list_clients as h

        kb_offline = h._kb_client_card(client_id=1, page=1, chunk=1, telegram_id=None)
        texts_offline = [btn.text for row in kb_offline.inline_keyboard for btn in row]
        self.assertNotIn("💬 Написать клиенту", texts_offline)

        kb_online = h._kb_client_card(client_id=1, page=1, chunk=1, telegram_id=123)
        self.assertEqual(["➕ Записать клиента", "📅 История записей"], [b.text for b in kb_online.inline_keyboard[0]])
        self.assertEqual(["💬 Написать клиенту"], [b.text for b in kb_online.inline_keyboard[1]])
