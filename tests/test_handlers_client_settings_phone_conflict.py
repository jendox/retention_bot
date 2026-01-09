from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class MemoryState:
    def __init__(self) -> None:
        self._data: dict = {}
        self._state = None

    async def get_data(self) -> dict:
        return dict(self._data)

    async def update_data(self, **kwargs) -> None:
        self._data.update(kwargs)

    async def set_state(self, state) -> None:
        self._state = state

    async def clear(self) -> None:
        self._data = {}
        self._state = None


class ClientSettingsPhoneConflictTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_phone_conflict_rerenders_prompt_and_skips_update(self) -> None:
        from src.handlers.client import settings as s

        state = MemoryState()
        await state.update_data(**{s.SETTINGS_MAIN_KEY: {"chat_id": 10, "message_id": 20}})

        message = SimpleNamespace(
            from_user=SimpleNamespace(id=777),
            text="375291234567",
            bot=SimpleNamespace(),
        )

        with (
            patch.object(s, "rate_limit_message", AsyncMock(return_value=True)),
            patch.object(s, "track_message", AsyncMock()),
            patch.object(s, "cleanup_messages", AsyncMock()),
            patch.object(s, "validate_phone", lambda _: "+375291234567"),
            patch.object(s, "_load_client_details", AsyncMock(return_value=SimpleNamespace(id=1))),
            patch.object(s, "_has_phone_conflict_for_client", AsyncMock(return_value=True)),
            patch.object(s, "safe_bot_edit_message_text", AsyncMock()) as edit_main,
        ):
            await s.save_phone(message=message, state=state, rate_limiter=None)

        self.assertTrue(edit_main.await_count >= 1)
