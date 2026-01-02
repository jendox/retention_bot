from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.notifications.types import NotificationEvent


@asynccontextmanager
async def _fake_session_local():
    yield object()


@asynccontextmanager
async def _fake_active_session(*args, **kwargs):
    yield object()


class MasterScheduleHandlerTests(unittest.IsolatedAsyncioTestCase):
    def test_button_text_escapes_client_name(self) -> None:
        from src.handlers.master import schedule as h
        from src.schemas.enums import BookingStatus

        booking = SimpleNamespace(
            id=1,
            start_at=datetime(2025, 12, 31, 10, 30, tzinfo=UTC),
            status=BookingStatus.CONFIRMED,
            client=SimpleNamespace(name="<b>X</b>"),
        )
        text = h._button_text(booking, tz=h.ZoneInfo("UTC"), scope=h.Scope.TODAY)
        self.assertIn("&lt;b&gt;X&lt;/b&gt;", text)
        self.assertNotIn("<b>", text)

    def test_history_buttons_show_attendance_badge_for_confirmed(self) -> None:
        from src.handlers.master import schedule as h
        from src.schemas.enums import AttendanceOutcome, BookingStatus

        base = dict(
            id=1,
            start_at=datetime(2025, 12, 31, 10, 30, tzinfo=UTC),
            status=BookingStatus.CONFIRMED,
            client=SimpleNamespace(name="C"),
        )

        attended = SimpleNamespace(**base, attendance_outcome=AttendanceOutcome.ATTENDED)
        no_show = SimpleNamespace(**base, attendance_outcome=AttendanceOutcome.NO_SHOW)
        unknown = SimpleNamespace(**base, attendance_outcome=AttendanceOutcome.UNKNOWN)

        self.assertTrue(h._button_text(attended, tz=h.ZoneInfo("UTC"), scope=h.Scope.YESTERDAY).startswith("✅ "))
        self.assertTrue(h._button_text(no_show, tz=h.ZoneInfo("UTC"), scope=h.Scope.YESTERDAY).startswith("🔴 "))
        self.assertTrue(h._button_text(unknown, tz=h.ZoneInfo("UTC"), scope=h.Scope.YESTERDAY).startswith("🕒 "))

    def test_history_buttons_keep_status_badge_for_non_confirmed(self) -> None:
        from src.handlers.master import schedule as h
        from src.schemas.enums import BookingStatus

        booking = SimpleNamespace(
            id=1,
            start_at=datetime(2025, 12, 31, 10, 30, tzinfo=UTC),
            status=BookingStatus.CANCELLED,
            client=SimpleNamespace(name="C"),
        )
        text = h._button_text(booking, tz=h.ZoneInfo("UTC"), scope=h.Scope.HISTORY_WEEK)
        self.assertTrue(text.startswith("🚫 "))

    async def test_cancel_enqueues_client_notification(self) -> None:
        from src.handlers.master import schedule as h
        from src.schemas.enums import Timezone

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            data="m:a:cancel_yes:7:s:today:p:1",
            message=SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock()),
            bot=SimpleNamespace(send_message=AsyncMock()),
            answer=AsyncMock(),
        )

        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True))

        booking = SimpleNamespace(
            id=7,
            start_at=datetime(2025, 12, 31, 10, 30, tzinfo=UTC),
            duration_min=60,
            master=SimpleNamespace(id=1, name="M", notify_clients=True),
            client=SimpleNamespace(
                id=2,
                name="C",
                telegram_id=123,
                timezone=Timezone("Europe/Minsk"),
                notifications_enabled=True,
            ),
        )

        outbox = SimpleNamespace(enqueue_booking_client_notification=AsyncMock(return_value=1))

        class _BookingRepo:
            def __init__(self, session) -> None:
                pass

            async def get_for_review(self, booking_id: int):
                return booking

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

            async def get_plan(self, *, master_id: int, now=None):
                return SimpleNamespace(is_pro=True)

        with (
            patch.object(h, "_fetch_master", AsyncMock(return_value=SimpleNamespace(id=1))),
            patch.object(h, "_cancel_booking", AsyncMock(return_value=True)),
            patch.object(h, "_send_schedule", AsyncMock()),
            patch.object(h, "session_local", _fake_session_local),
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "BookingRepository", _BookingRepo),
            patch.object(h, "EntitlementsService", _Entitlements),
            patch.object(h, "ScheduledNotificationRepository", lambda _s: outbox),
        ):
            await h.master_booking_actions(callback=callback, state=SimpleNamespace(), notifier=notifier)

        outbox.enqueue_booking_client_notification.assert_awaited()
        kwargs = outbox.enqueue_booking_client_notification.await_args.kwargs
        self.assertEqual(kwargs["event"], NotificationEvent.BOOKING_CANCELLED_BY_MASTER.value)
        self.assertEqual(kwargs["booking_id"], 7)

    async def test_cancel_shows_confirmation_prompt(self) -> None:
        from src.handlers.master import schedule as h

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            data="m:a:cancel:7:s:today:p:1",
            message=SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock()),
            bot=SimpleNamespace(send_message=AsyncMock()),
            answer=AsyncMock(),
        )
        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True))
        show_prompt = AsyncMock()
        cancel_booking = AsyncMock()
        with (
            patch.object(h, "_send_cancel_confirm_card", show_prompt),
            patch.object(h, "_cancel_booking", cancel_booking),
        ):
            await h.master_booking_actions(callback=callback, state=SimpleNamespace(), notifier=notifier)

        show_prompt.assert_awaited()
        cancel_booking.assert_not_awaited()

    async def test_attendance_action_marks_and_refreshes_card(self) -> None:
        from src.handlers.master import schedule as h

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            data="m:a:attended:7:s:yesterday:p:1",
            message=SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock()),
            bot=SimpleNamespace(send_message=AsyncMock()),
            answer=AsyncMock(),
        )
        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True))

        class _FakeUseCase:
            def __init__(self, session) -> None:
                pass

            async def execute(self, request):
                return SimpleNamespace(ok=True, error=None)

        with (
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "_send_booking_card", AsyncMock()) as send_card,
            patch("src.use_cases.mark_booking_attendance.MarkBookingAttendance", _FakeUseCase),
        ):
            await h.master_booking_actions(callback=callback, state=SimpleNamespace(), notifier=notifier)

        send_card.assert_awaited()
