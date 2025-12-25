from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


@asynccontextmanager
async def _fake_active_session():
    yield object()


@asynccontextmanager
async def _fake_session_local():
    yield object()


class ClientListBookingsHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_sends_master_notification_via_notifier(self) -> None:
        from src.handlers.client import list_bookings as h
        from src.schemas.enums import Timezone

        booking = SimpleNamespace(
            id=7,
            start_at=datetime(2025, 12, 31, 10, 30, tzinfo=UTC),
            duration_min=60,
            master=SimpleNamespace(telegram_id=777, name="M", timezone=Timezone("Europe/Minsk")),
            client=SimpleNamespace(id=2, name="C"),
        )

        class _ClientRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(id=2)

        class _BookingRepo:
            def __init__(self, session) -> None:
                pass

            async def get_for_review(self, booking_id: int):
                return booking

            async def cancel_by_client(self, *, client_id: int, booking_id: int) -> bool:
                return True

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            data="c:booking:7:cancel",
            message=SimpleNamespace(edit_text=AsyncMock()),
            bot=SimpleNamespace(send_message=AsyncMock()),
            answer=AsyncMock(),
        )
        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True))

        with (
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "ClientRepository", _ClientRepo),
            patch.object(h, "BookingRepository", _BookingRepo),
        ):
            await h.client_cancel_booking_legacy(callback=callback, notifier=notifier)

        notifier.maybe_send.assert_awaited()
        request = notifier.maybe_send.await_args.args[0]
        self.assertEqual(request.event.value, "booking_cancelled_by_client")
        self.assertEqual(request.chat_id, 777)

    async def test_start_lists_bookings_escapes_master_name(self) -> None:
        from src.handlers.client import list_bookings as h
        from src.schemas.enums import BookingStatus, Timezone

        booking = SimpleNamespace(
            id=1,
            start_at=datetime.now(UTC) + timedelta(days=2),
            status=BookingStatus.CONFIRMED,
            master=SimpleNamespace(name="<b>X</b>"),
        )

        sent = h._build_booking_row_text(index=1, booking=booking, client_timezone=Timezone("Europe/Minsk"))
        self.assertIn("&lt;b&gt;X&lt;/b&gt;", sent)
        self.assertNotIn("<b>X</b>", sent)

    async def test_close_falls_back_to_hiding_keyboard_on_delete_race(self) -> None:
        from src.handlers.client import list_bookings as h

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            data="c:bookings:close",
            answer=AsyncMock(),
            message=SimpleNamespace(
                delete=AsyncMock(),
                edit_reply_markup=AsyncMock(),
            ),
            bot=SimpleNamespace(),
        )

        state = SimpleNamespace(
            get_data=AsyncMock(return_value={h.LIST_BOOKINGS_MAIN_KEY: {"chat_id": 10, "message_id": 1}}),
            set_data=AsyncMock(),
        )

        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=False))
        with patch.object(h, "safe_delete", AsyncMock(return_value=False)):
            await h.client_bookings_callbacks(callback=callback, state=state, notifier=notifier)

        callback.message.edit_reply_markup.assert_awaited_with(reply_markup=None)
