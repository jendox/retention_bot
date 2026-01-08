from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class PersonalDataPolicySendTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_personal_data_policy_sends_document(self) -> None:
        from src.handlers.shared.personal_data_policy import send_personal_data_policy

        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
            tmp.write(b"%PDF-1.4\\n%fake\\n")
            tmp.flush()

            settings = SimpleNamespace(security=SimpleNamespace(personal_data_policy_path=tmp.name))
            bot = SimpleNamespace(send_document=AsyncMock(), send_message=AsyncMock())

            with patch("src.handlers.shared.personal_data_policy.get_settings", return_value=settings):
                ok = await send_personal_data_policy(bot=bot, chat_id=123)

            self.assertTrue(ok)
            bot.send_document.assert_awaited()
            bot.send_message.assert_not_awaited()
