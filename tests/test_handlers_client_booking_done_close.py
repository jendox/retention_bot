from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


@asynccontextmanager
async def _fake_active_session():
    yield object()


class _State:
    def __init__(self, data: dict) -> None:
        self._data = dict(data)

    async def get_data(self) -> dict:
        return dict(self._data)

    async def clear(self) -> None:
        self._data = {}


class ClientBookingDoneCloseTests(unittest.IsolatedAsyncioTestCase):
    async def test_done_message_has_close_button(self) -> None:
        from src.handlers.client import booking as h
        from src.notifications.close import NOTIFICATION_CLOSE_CB
        from src.schemas.enums import Timezone

        slot_utc = datetime.now(UTC) + timedelta(days=2)
        state = _State(
            {
                "booking_slots_utc": [slot_utc.isoformat()],
                "selected_slot_index": 0,
                "master_id": 1,
                "client_id": 2,
                "client_name": "Client",
            },
        )

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(),
            message=SimpleNamespace(answer=AsyncMock()),
            answer=AsyncMock(),
        )

        class _UC:
            def __init__(self, session) -> None:  # noqa: ARG002
                pass

            async def execute(self, request):  # noqa: ARG002
                return SimpleNamespace(
                    ok=True,
                    booking=SimpleNamespace(id=7),
                    master=SimpleNamespace(
                        id=1,
                        name="M",
                        telegram_id=123,
                        timezone=Timezone("Europe/Minsk"),
                        slot_size_min=60,
                    ),
                    warn_master_bookings_near_limit=False,
                    plan_is_pro=True,
                    usage=None,
                    bookings_limit=None,
                    error=None,
                )

        class _LinkRepo:
            def __init__(self, session) -> None:  # noqa: ARG002
                pass

            async def get_client_alias(self, *, master_id: int, client_id: int) -> str | None:  # noqa: ARG002
                return None

        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True), policy=SimpleNamespace())

        with (
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "CreateClientBooking", _UC),
            patch.object(h, "MasterClientRepository", _LinkRepo),
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "cleanup_messages", AsyncMock()),
        ):
            await h._booking_confirm_impl(callback=callback, state=state, notifier=notifier, rate_limiter=None)

        _kwargs = callback.message.answer.await_args.kwargs
        markup = _kwargs.get("reply_markup")
        self.assertIsNotNone(markup)
        close_buttons = [
            btn
            for row in markup.inline_keyboard
            for btn in row
            if getattr(btn, "callback_data", None) == NOTIFICATION_CLOSE_CB
        ]
        self.assertTrue(close_buttons)
