from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class ClientListMastersAliasCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_alias_cleans_up_without_success_message(self) -> None:
        from src.handlers.client import list_masters as h

        message = SimpleNamespace(
            text="New name",
            from_user=SimpleNamespace(id=777),
            bot=SimpleNamespace(),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(
            update_data=AsyncMock(),
            set_state=AsyncMock(),
            clear=AsyncMock(),
        )

        mock_track_message = AsyncMock()
        mock_cleanup_messages = AsyncMock()
        mock_save_alias_impl = AsyncMock(return_value=True)

        with (
            patch.object(h, "rate_limit_message", AsyncMock(return_value=True)),
            patch.object(h, "track_message", mock_track_message),
            patch.object(h, "cleanup_messages", mock_cleanup_messages),
            patch.object(h, "_save_alias_impl", mock_save_alias_impl),
        ):
            await h.save_alias(message=message, state=state)

        mock_cleanup_messages.assert_awaited_once_with(state, message.bot, bucket=h.EDIT_ALIAS_BUCKET)
        mock_track_message.assert_awaited_once_with(state, message, bucket=h.EDIT_ALIAS_BUCKET)
        state.update_data.assert_awaited_once_with(**{h.EDIT_ALIAS_KEY: {}})
        state.set_state.assert_awaited_once_with(None)
        message.answer.assert_not_awaited()
