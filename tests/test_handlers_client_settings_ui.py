from __future__ import annotations

import unittest


class ClientSettingsUiTests(unittest.TestCase):
    def test_delete_button_is_present(self) -> None:
        from src.handlers.client import settings as h

        kb = h._kb_settings(notifications_enabled=True)
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data == f"{h.SETTINGS_CB_PREFIX}delete_data":
                    self.assertIn("Удалить", btn.text)
                    return
        raise AssertionError("delete button not found")
