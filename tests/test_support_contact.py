from __future__ import annotations

import unittest


class SupportContactTests(unittest.TestCase):
    def test_build_support_keyboard_for_at_contact(self) -> None:
        from src.handlers.shared.support_contact import build_support_keyboard

        kb = build_support_keyboard(contact="@beautydesk_support")
        self.assertIsNotNone(kb)
        self.assertEqual(2, len(kb.inline_keyboard))
        btn = kb.inline_keyboard[0][0]
        self.assertEqual("💬 Написать в поддержку", btn.text)
        self.assertEqual("https://t.me/beautydesk_support", btn.url)
        close_btn = kb.inline_keyboard[1][0]
        self.assertEqual("paywall:close", close_btn.callback_data)
