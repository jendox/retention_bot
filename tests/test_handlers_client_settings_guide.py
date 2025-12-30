from __future__ import annotations

import unittest


class ClientSettingsGuideTests(unittest.TestCase):
    def test_parse_action_supports_guide_entry_and_pages(self) -> None:
        from src.handlers.client import settings as h

        action, arg = h._parse_action(f"{h.SETTINGS_CB_PREFIX}guide")
        self.assertEqual(action, "guide")
        self.assertIsNone(arg)

        action, arg = h._parse_action(f"{h.SETTINGS_CB_PREFIX}guide:2")
        self.assertEqual(action, "guide_page")
        self.assertEqual(arg, "2")

    def test_guide_keyboard_has_back_to_menu_and_close(self) -> None:
        from src.handlers.client import settings as h

        kb = h._kb_guide(page=0, total=3)
        callback_data = {btn.callback_data for row in kb.inline_keyboard for btn in row}
        self.assertIn(f"{h.SETTINGS_CB_PREFIX}back_menu", callback_data)
        self.assertIn(f"{h.SETTINGS_CB_PREFIX}back", callback_data)
