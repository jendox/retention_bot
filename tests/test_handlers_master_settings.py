from __future__ import annotations

import unittest


class MasterSettingsUiTests(unittest.TestCase):
    def test_notify_button_text_pro_varies_by_state(self) -> None:
        from src.handlers.master import settings as h

        kb_on = h._kb_settings(notify_clients=True, plan_is_pro=True)
        kb_off = h._kb_settings(notify_clients=False, plan_is_pro=True)

        def _notify_text(kb) -> str:
            for row in kb.inline_keyboard:
                for btn in row:
                    if btn.callback_data == f"{h.SETTINGS_CB_PREFIX}notify":
                        return btn.text
            raise AssertionError("notify button not found")

        self.assertIn("включены", _notify_text(kb_on))
        self.assertIn("выключены", _notify_text(kb_off))

    def test_notify_button_text_free_shows_pro_lock(self) -> None:
        from src.handlers.master import settings as h

        kb = h._kb_settings(notify_clients=True, plan_is_pro=False)

        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data == f"{h.SETTINGS_CB_PREFIX}notify":
                    self.assertIn("Pro", btn.text)
                    self.assertIn("🔒", btn.text)
                    return
        raise AssertionError("notify button not found")
