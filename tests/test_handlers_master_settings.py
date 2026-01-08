from __future__ import annotations

import unittest


class MasterSettingsUiTests(unittest.TestCase):
    def test_edit_profile_first_row_has_name_phone_tz(self) -> None:
        from src.handlers.master import settings as h

        kb = h._kb_settings_edit_profile(notify_clients=True, notify_attendance=True, plan_is_pro=True)
        self.assertGreaterEqual(len(kb.inline_keyboard), 1)
        row = kb.inline_keyboard[0]
        self.assertEqual(3, len(row))
        self.assertEqual(f"{h.SETTINGS_CB_PREFIX}name", row[0].callback_data)
        self.assertEqual(f"{h.SETTINGS_CB_PREFIX}phone", row[1].callback_data)
        self.assertEqual(f"{h.SETTINGS_CB_PREFIX}tz", row[2].callback_data)

    def test_notify_button_text_pro_varies_by_state(self) -> None:
        from src.handlers.master import settings as h

        kb_on = h._kb_settings_edit_profile(notify_clients=True, notify_attendance=True, plan_is_pro=True)
        kb_off = h._kb_settings_edit_profile(notify_clients=False, notify_attendance=True, plan_is_pro=True)

        def _notify_text(kb) -> str:
            for row in kb.inline_keyboard:
                for btn in row:
                    if btn.callback_data == f"{h.SETTINGS_CB_PREFIX}notify":
                        return btn.text
            raise AssertionError("notify button not found")

        self.assertIn("включено", _notify_text(kb_on))
        self.assertIn("выключено", _notify_text(kb_off))

    def test_notify_button_text_free_shows_pro_lock(self) -> None:
        from src.handlers.master import settings as h

        kb = h._kb_settings_edit_profile(notify_clients=True, notify_attendance=True, plan_is_pro=False)

        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data == f"{h.SETTINGS_CB_PREFIX}notify":
                    self.assertIn("Pro", btn.text)
                    self.assertIn("🔒", btn.text)
                    return
        raise AssertionError("notify button not found")

    def test_notify_attendance_button_text_free_shows_pro_lock(self) -> None:
        from src.handlers.master import settings as h

        kb = h._kb_settings_edit_profile(notify_clients=True, notify_attendance=True, plan_is_pro=False)
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data == f"{h.SETTINGS_CB_PREFIX}notify_attendance":
                    self.assertIn("Pro", btn.text)
                    self.assertIn("🔒", btn.text)
                    return
        raise AssertionError("notify_attendance button not found")

    def test_personal_data_button_is_present(self) -> None:
        from src.handlers.master import settings as h

        kb = h._kb_settings_hub()
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data == f"{h.SETTINGS_CB_PREFIX}personal_data":
                    self.assertIn("Персональные", btn.text)
                    return
        raise AssertionError("personal_data button not found")

    def test_personal_data_menu_order(self) -> None:
        from src.handlers.master import settings as h

        kb = h._kb_personal_data_menu()
        self.assertEqual(3, len(kb.inline_keyboard))
        self.assertEqual(f"{h.SETTINGS_CB_PREFIX}back_menu", kb.inline_keyboard[0][0].callback_data)
        self.assertEqual(f"{h.SETTINGS_CB_PREFIX}pd_policy", kb.inline_keyboard[1][0].callback_data)
        self.assertEqual(f"{h.SETTINGS_CB_PREFIX}delete_data", kb.inline_keyboard[2][0].callback_data)

    def test_edit_profile_button_is_present(self) -> None:
        from src.handlers.master import settings as h

        kb = h._kb_settings_hub()
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data == f"{h.SETTINGS_CB_PREFIX}edit_profile":
                    self.assertIn("Редактировать", btn.text)
                    return
        raise AssertionError("edit_profile button not found")
