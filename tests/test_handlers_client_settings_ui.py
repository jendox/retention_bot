from __future__ import annotations

import unittest


class ClientSettingsUiTests(unittest.TestCase):
    def test_personal_data_button_is_present(self) -> None:
        from src.handlers.client import settings as h

        kb = h._kb_settings_hub()
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data == f"{h.SETTINGS_CB_PREFIX}personal_data":
                    self.assertIn("Персональные", btn.text)
                    return
        raise AssertionError("personal_data button not found")

    def test_support_button_is_present(self) -> None:
        from src.handlers.client import settings as h

        kb = h._kb_settings_hub()
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data == f"{h.SETTINGS_CB_PREFIX}support":
                    self.assertIn("Поддержка", btn.text)
                    return
        raise AssertionError("support button not found")

    def test_personal_data_menu_order(self) -> None:
        from src.handlers.client import settings as h

        kb = h._kb_personal_data_menu()
        self.assertEqual(3, len(kb.inline_keyboard))
        self.assertEqual(f"{h.SETTINGS_CB_PREFIX}back_menu", kb.inline_keyboard[0][0].callback_data)
        self.assertEqual(f"{h.SETTINGS_CB_PREFIX}pd_policy", kb.inline_keyboard[1][0].callback_data)
        self.assertEqual(f"{h.SETTINGS_CB_PREFIX}delete_data", kb.inline_keyboard[2][0].callback_data)
