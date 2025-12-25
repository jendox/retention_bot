from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.schemas.enums import BookingStatus


@asynccontextmanager
async def _fake_active_session():
    yield object()


class MasterBookingReviewHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirm_success_edits_text_and_notifies(self) -> None:
        from src.handlers.master import booking_review as h
        from src.schemas.enums import Timezone

        booking = SimpleNamespace(
            id=7,
            start_at=datetime.now(UTC) + timedelta(days=1),
            duration_min=60,
            master=SimpleNamespace(id=1, name="M", timezone=Timezone("Europe/Minsk"), notify_clients=True),
            client=SimpleNamespace(
                id=2,
                name="<b>C</b>",
                telegram_id=123,
                timezone=Timezone("Europe/Minsk"),
                notifications_enabled=True,
            ),
        )

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            data="m:booking:7:confirm",
            message=SimpleNamespace(edit_reply_markup=AsyncMock(), edit_text=AsyncMock()),
            answer=AsyncMock(),
        )

        class _UC:
            def __init__(self, session) -> None:
                pass

            async def execute(self, request):
                return SimpleNamespace(
                    ok=True,
                    booking=booking,
                    new_status=BookingStatus.CONFIRMED,
                    plan_is_pro=True,
                    error=None,
                )

        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True))

        with (
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "ReviewMasterBooking", _UC),
        ):
            await h.master_review_booking(callback=callback, notifier=notifier)

        callback.message.edit_text.assert_awaited()
        edited_text = callback.message.edit_text.await_args.args[0]
        self.assertIn("&lt;b&gt;C&lt;/b&gt;", edited_text)
        notifier.maybe_send.assert_awaited()

    async def test_already_handled_shows_alert(self) -> None:
        from src.handlers.master import booking_review as h

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            data="m:booking:7:confirm",
            message=SimpleNamespace(edit_reply_markup=AsyncMock(), edit_text=AsyncMock()),
            answer=AsyncMock(),
        )

        class _UC:
            def __init__(self, session) -> None:
                pass

            async def execute(self, request):
                return SimpleNamespace(ok=False, error=h.ReviewMasterBookingError.ALREADY_HANDLED)

        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True))

        with (
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "ReviewMasterBooking", _UC),
        ):
            await h.master_review_booking(callback=callback, notifier=notifier)

        callback.answer.assert_awaited()
        notifier.maybe_send.assert_not_awaited()
