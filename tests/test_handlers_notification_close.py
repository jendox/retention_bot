from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class NotificationCloseHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_deletes_message(self) -> None:
        from src.handlers import notification_close as h

        callback = SimpleNamespace(
            data=h.NOTIFICATION_CLOSE_CB,
            from_user=SimpleNamespace(id=1),
            bot=SimpleNamespace(),
            message=SimpleNamespace(),
            answer=AsyncMock(),
        )
        state = SimpleNamespace()

        mock_safe_delete = AsyncMock(return_value=True)
        with (
            patch.object(h, "rate_limit_callback", AsyncMock(return_value=True)),
            patch.object(h, "safe_delete", mock_safe_delete),
        ):
            await h.notification_close(callback=callback, state=state)

        callback.answer.assert_awaited_once()
        mock_safe_delete.assert_awaited_once()
