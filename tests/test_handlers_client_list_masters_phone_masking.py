from __future__ import annotations

import unittest


class ClientListMastersPhoneMaskingTests(unittest.TestCase):
    def test_list_page_masks_phone(self) -> None:
        from src.handlers.client import list_masters as h

        masters = [{"id": 1, "name": "Ann", "phone": "+375291234567"}]
        text = h._render_list_page(masters=masters, page=1)
        self.assertIn("*****", text)
        self.assertNotIn("+375291234567", text)

    def test_select_buttons_mask_phone(self) -> None:
        from src.handlers.client import list_masters as h

        masters = [{"id": 1, "name": "Ann", "phone": "+375291234567"}]
        kb = h._kb_select(masters=masters, page=1, chunk=h.CHUNK_1)
        first = kb.inline_keyboard[0][0].text
        self.assertIn("*****", first)
        self.assertNotIn("+375291234567", first)
