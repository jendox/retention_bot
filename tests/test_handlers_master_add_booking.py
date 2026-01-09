from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.notifications.policy import DefaultNotificationPolicy
from src.use_cases.create_master_booking import CreateMasterBookingError, CreateMasterBookingResult
from src.use_cases.entitlements import Usage


class MemoryState:
    def __init__(self) -> None:
        self._data: dict = {}
        self._state = None

    async def get_data(self) -> dict:
        return dict(self._data)

    async def set_data(self, data: dict) -> None:
        self._data = dict(data)

    async def update_data(self, **kwargs) -> None:
        self._data.update(kwargs)

    async def set_state(self, state) -> None:
        self._state = state

    async def clear(self) -> None:
        self._data = {}
        self._state = None


@asynccontextmanager
async def _fake_active_session():
    yield object()


@asynccontextmanager
async def _fake_session_local():
    yield object()


class MasterAddBookingHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_no_clients_shows_add_client_prompt(self) -> None:
        from src.handlers.master import add_booking as h

        state = MemoryState()
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(),
        )

        answer_tracked = AsyncMock()
        with (
            patch.object(h, "_reset_add_booking", AsyncMock()),
            patch.object(h, "track_message", AsyncMock()),
            patch.object(h, "_load_master_with_clients", AsyncMock(return_value=SimpleNamespace(clients=[]))),
            patch.object(h, "answer_tracked", answer_tracked),
        ):
            await h.start_add_booking(message=message, state=state)

        self.assertEqual(state._state, h.AddBookingStates.no_clients)
        answer_tracked.assert_awaited()
        reply_markup = answer_tracked.await_args.kwargs["reply_markup"]
        self.assertEqual(reply_markup.inline_keyboard[0][0].callback_data, h.NO_CLIENTS_ADD_CLIENT_CB)
        self.assertEqual(reply_markup.inline_keyboard[1][0].callback_data, "m:add_booking:cancel")

    async def test_pick_date_no_slots_keeps_calendar_and_answers(self) -> None:
        from src.handlers.master import add_booking as h

        state = MemoryState()
        await state.update_data(
            master_id=1,
            master_timezone="Europe/Minsk",
        )

        picked_day = datetime.now(UTC).date() + timedelta(days=1)
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            data=f"{h.MONTH_CAL_PREFIX}:d:{picked_day:%Y%m%d}",
        )

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

            async def max_booking_horizon_days(self, *, master_id: int):
                return 30

        class _Slots:
            def __init__(self, session) -> None:
                pass

            async def execute(self, *, master_id, client_day, client_tz):
                return SimpleNamespace(slots_utc=[], master_day=client_day)

        limits = h.month_calendar.CalendarLimits(
            today=picked_day - timedelta(days=1),
            min_date=picked_day,
            max_date=picked_day + timedelta(days=29),
            pro_max_date=picked_day + timedelta(days=60),
            plan_is_pro=True,
        )
        with (
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "rate_limit_callback", AsyncMock(return_value=True)),
            patch.object(h, "_calendar_limits", AsyncMock(return_value=limits)),
            patch.object(h, "safe_edit_text", AsyncMock(return_value=True)),
            patch.object(h, "session_local", _fake_session_local),
            patch.object(h, "EntitlementsService", _Entitlements),
            patch.object(h, "GetMasterFreeSlots", _Slots),
        ):
            await h.pick_date(callback=callback, state=state)

        callback.answer.assert_awaited()

    async def test_pick_date_out_of_range_restores_calendar(self) -> None:
        from src.handlers.master import add_booking as h

        state = MemoryState()
        await state.update_data(
            master_id=1,
            master_timezone="Europe/Minsk",
        )

        picked_day = datetime.now(UTC).date()
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            data=f"{h.MONTH_CAL_PREFIX}:d:{picked_day:%Y%m%d}",
        )

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

            async def max_booking_horizon_days(self, *, master_id: int):
                return 0  # only today allowed

        class _Slots:
            def __init__(self, session) -> None:
                pass

            async def execute(self, *, master_id, client_day, client_tz):
                return SimpleNamespace(slots_utc=[], master_day=client_day)

        limits = h.month_calendar.CalendarLimits(
            today=picked_day,
            min_date=picked_day + timedelta(days=1),
            max_date=picked_day + timedelta(days=7),
            pro_max_date=picked_day + timedelta(days=60),
            plan_is_pro=False,
        )
        with (
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "rate_limit_callback", AsyncMock(return_value=True)),
            patch.object(h, "_calendar_limits", AsyncMock(return_value=limits)),
            patch.object(h, "safe_edit_text", AsyncMock(return_value=True)),
            patch.object(h, "session_local", _fake_session_local),
            patch.object(h, "EntitlementsService", _Entitlements),
            patch.object(h, "GetMasterFreeSlots", _Slots),
        ):
            await h.pick_date(callback=callback, state=state)

        callback.answer.assert_awaited()

    async def test_search_no_matches_shows_cancel_button(self) -> None:
        from src.handlers.master import add_booking as h

        state = MemoryState()
        await state.set_state(h.AddBookingStates.search_client)

        message = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            text="no matches",
            bot=SimpleNamespace(),
        )

        class _Master:
            clients: list = []

        answer_tracked = AsyncMock()
        with (
            patch.object(h, "track_message", AsyncMock()),
            patch.object(h, "_load_master_with_clients", AsyncMock(return_value=_Master())),
            patch.object(h, "answer_tracked", answer_tracked),
        ):
            await h.search_client(message=message, state=state)

        reply_markup = answer_tracked.await_args.kwargs.get("reply_markup")
        self.assertIsNotNone(reply_markup)
        self.assertEqual(reply_markup.inline_keyboard[0][0].callback_data, "m:add_booking:cancel")

    async def test_calendar_cancel_restores_clients_list(self) -> None:
        from src.handlers.master import add_booking as h

        state = MemoryState()
        await state.update_data(
            master_id=1,
            master_timezone="Europe/Minsk",
            clients=[{"id": 2, "name": "A", "phone": "+375", "telegram_id": None}],
        )
        await state.set_state(h.AddBookingStates.selecting_date)

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            data=f"{h.MONTH_CAL_PREFIX}:cancel",
        )

        with (
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "rate_limit_callback", AsyncMock(return_value=True)),
            patch.object(
                h,
                "_calendar_limits",
                AsyncMock(
                    return_value=h.month_calendar.CalendarLimits(
                        today=date(2026, 1, 2),
                        min_date=date(2026, 1, 3),
                        max_date=date(2026, 1, 9),
                        pro_max_date=date(2026, 3, 2),
                        plan_is_pro=False,
                    ),
                ),
            ),
            patch.object(h, "safe_edit_text", AsyncMock(return_value=True)),
        ):
            await h.pick_date(callback=callback, state=state)

        self.assertEqual(state._state, h.AddBookingStates.search_client)
        callback.answer.assert_awaited()

    async def test_back_from_slots_restores_calendar(self) -> None:
        from src.handlers.master import add_booking as h

        state = MemoryState()
        await state.update_data(master_id=1, master_timezone="Europe/Minsk", client={"id": 2})
        await state.set_state(h.AddBookingStates.selecting_slot)

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            data=h.SLOTS_BACK_TO_CALENDAR_CB,
        )

        restore = AsyncMock()
        with (
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "_restore_calendar", restore),
        ):
            await h.back_to_calendar(callback=callback, state=state)

        self.assertEqual(state._state, h.AddBookingStates.selecting_date)
        callback.answer.assert_awaited()
        restore.assert_awaited()

    async def test_back_from_confirm_restores_slots(self) -> None:
        from src.handlers.master import add_booking as h

        slot = datetime.now(UTC) + timedelta(days=1)
        state = MemoryState()
        await state.update_data(
            master_id=1,
            master_timezone="Europe/Minsk",
            slots=[slot.isoformat()],
            master_day=slot.date().isoformat(),
            selected_slot=slot.isoformat(),
            client={"id": 2},
            confirm_in_progress=False,
        )
        await state.set_state(h.AddBookingStates.confirm)

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            data=h.CONFIRM_BACK_TO_SLOTS_CB,
        )

        safe_edit = AsyncMock(return_value=True)
        with (
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "safe_edit_text", safe_edit),
        ):
            await h.back_to_slots(callback=callback, state=state)

        self.assertEqual(state._state, h.AddBookingStates.selecting_slot)
        callback.answer.assert_awaited()

    async def test_confirm_quota_exceeded_resets_state(self) -> None:
        from src.handlers.master import add_booking as h

        state = MemoryState()
        slot = datetime.now(UTC) + timedelta(days=1)
        await state.update_data(
            confirm_in_progress=False,
            selected_slot=slot.isoformat(),
            client={
                "id": 2,
                "telegram_id": None,
                "name": "N",
                "timezone": "Europe/Minsk",
                "notifications_enabled": True,
            },
            master_id=1,
            master_timezone="Europe/Minsk",
            slots=[slot.isoformat()],
            master_day=slot.date().isoformat(),
        )

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(
                message_id=123,
                edit_reply_markup=AsyncMock(),
                edit_text=AsyncMock(),
                answer=AsyncMock(),
            ),
            answer=AsyncMock(),
        )

        class _UC:
            def __init__(self, session) -> None:
                pass

            async def execute(self, request):
                return CreateMasterBookingResult(
                    ok=False,
                    error=CreateMasterBookingError.QUOTA_EXCEEDED,
                    plan_is_pro=False,
                    bookings_limit=10,
                    usage=Usage(clients_count=0, bookings_created_this_month=10),
                )

        cleanup = AsyncMock()
        safe_edit = AsyncMock()
        with (
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "CreateMasterBooking", _UC),
            patch.object(h, "cleanup_messages", cleanup),
            patch.object(h, "safe_edit_text", safe_edit),
        ):
            await h.confirm_booking(
                callback=callback,
                state=state,
                notifier=SimpleNamespace(maybe_send=AsyncMock(return_value=False), policy=DefaultNotificationPolicy()),
            )

        cleanup.assert_awaited()
        safe_edit.assert_awaited()
        self.assertEqual(await state.get_data(), {})

    async def test_confirm_slot_taken_returns_to_slot_selection(self) -> None:
        from src.handlers.master import add_booking as h

        state = MemoryState()
        slot = datetime.now(UTC) + timedelta(days=1)
        await state.update_data(
            confirm_in_progress=False,
            selected_slot=slot.isoformat(),
            client={
                "id": 2,
                "telegram_id": None,
                "name": "N",
                "timezone": "Europe/Minsk",
                "notifications_enabled": True,
            },
            master_id=1,
            master_timezone="Europe/Minsk",
            slots=[slot.isoformat()],
            master_day=slot.date().isoformat(),
        )

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(edit_reply_markup=AsyncMock(), edit_text=AsyncMock(), answer=AsyncMock()),
            answer=AsyncMock(),
        )

        class _UC:
            def __init__(self, session) -> None:
                pass

            async def execute(self, request):
                return CreateMasterBookingResult(ok=False, error=CreateMasterBookingError.SLOT_NOT_AVAILABLE)

        with (
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "CreateMasterBooking", _UC),
        ):
            await h.confirm_booking(
                callback=callback,
                state=state,
                notifier=SimpleNamespace(maybe_send=AsyncMock(), policy=DefaultNotificationPolicy()),
            )

        self.assertEqual(state._state, h.AddBookingStates.selecting_slot)
        callback.message.edit_text.assert_awaited()
        data = await state.get_data()
        self.assertFalse(bool(data.get("confirm_in_progress")))

    async def test_smoke_happy_path_search_choose_date_slot_confirm(self) -> None:
        from src.handlers.master import add_booking as h
        from src.schemas.enums import Timezone

        state = MemoryState()

        bot = SimpleNamespace(send_message=AsyncMock())
        start_message = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            text="",
            bot=bot,
            chat=SimpleNamespace(id=10),
            answer=AsyncMock(),
        )

        class _Client:
            def __init__(self, client_id: int) -> None:
                self.id = client_id
                self.name = "Client"
                self.phone = None
                self.telegram_id = None
                self.timezone = Timezone.EUROPE_MINSK
                self.notifications_enabled = True

            def to_state_dict(self) -> dict:
                return {
                    "id": self.id,
                    "name": self.name,
                    "phone": self.phone,
                    "telegram_id": self.telegram_id,
                    "timezone": str(self.timezone.value),
                    "notifications_enabled": self.notifications_enabled,
                }

        master = SimpleNamespace(
            id=1,
            slot_size_min=60,
            timezone=Timezone.EUROPE_MINSK,
            clients=[_Client(2)],
        )

        now_utc = datetime.now(UTC)
        slot_utc = now_utc.replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=2)
        picked_day = slot_utc.date()

        class _Calendar:
            async def start_calendar(self):
                return SimpleNamespace()

            async def process_selection(self, callback, callback_data):
                return True, slot_utc

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

            async def max_booking_horizon_days(self, *, master_id: int):
                return 30

        class _Slots:
            def __init__(self, session) -> None:
                pass

            async def execute(self, *, master_id, client_day, client_tz):
                return SimpleNamespace(slots_utc=[slot_utc], master_day=client_day)

        class _Create:
            def __init__(self, session) -> None:
                pass

            async def execute(self, request):
                return CreateMasterBookingResult(
                    ok=True,
                    booking=SimpleNamespace(id=7),
                    master=SimpleNamespace(id=1, name="M", slot_size_min=60, notify_clients=True),
                )

        with (
            patch.object(h, "track_message", AsyncMock()),
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "rate_limit_callback", AsyncMock(return_value=True)),
            patch.object(h, "answer_tracked", AsyncMock()),
            patch.object(h, "_load_master_with_clients", AsyncMock(return_value=master)),
            patch.object(
                h,
                "_calendar_limits",
                AsyncMock(
                    return_value=h.month_calendar.CalendarLimits(
                        today=picked_day - timedelta(days=1),
                        min_date=picked_day,
                        max_date=picked_day + timedelta(days=29),
                        pro_max_date=picked_day + timedelta(days=60),
                        plan_is_pro=True,
                    ),
                ),
            ),
            patch.object(h, "safe_edit_text", AsyncMock(return_value=True)),
            patch.object(h, "session_local", _fake_session_local),
            patch.object(h, "EntitlementsService", _Entitlements),
            patch.object(h, "GetMasterFreeSlots", _Slots),
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "CreateMasterBooking", _Create),
            patch.object(h, "cleanup_messages", AsyncMock()),
        ):
            await h.start_add_booking(start_message, state)
            self.assertEqual(state._state, h.AddBookingStates.search_client)

            query_message = SimpleNamespace(
                from_user=SimpleNamespace(id=10),
                text="Cli",
                bot=bot,
                chat=SimpleNamespace(id=10),
                answer=AsyncMock(),
            )
            await h.search_client(query_message, state)

            choose_cb = SimpleNamespace(
                from_user=SimpleNamespace(id=10),
                data="m:add_booking:client:2",
                bot=bot,
                message=SimpleNamespace(edit_text=AsyncMock()),
                answer=AsyncMock(),
            )
            await h.choose_client(choose_cb, state)
            self.assertEqual(state._state, h.AddBookingStates.selecting_date)

            pick_date_cb = SimpleNamespace(
                from_user=SimpleNamespace(id=10),
                data=f"{h.MONTH_CAL_PREFIX}:d:{picked_day:%Y%m%d}",
                bot=bot,
                message=SimpleNamespace(edit_text=AsyncMock()),
                answer=AsyncMock(),
            )
            await h.pick_date(callback=pick_date_cb, state=state)
            self.assertEqual(state._state, h.AddBookingStates.selecting_slot)

            pick_slot_cb = SimpleNamespace(
                from_user=SimpleNamespace(id=10),
                data="m:add_booking:slot:0",
                bot=bot,
                message=SimpleNamespace(edit_text=AsyncMock()),
                answer=AsyncMock(),
            )
            await h.pick_slot(pick_slot_cb, state)
            self.assertEqual(state._state, h.AddBookingStates.confirm)

            confirm_cb = SimpleNamespace(
                from_user=SimpleNamespace(id=10),
                data="m:add_booking:confirm",
                bot=bot,
                message=SimpleNamespace(edit_reply_markup=AsyncMock()),
                answer=AsyncMock(),
            )
            await h.confirm_booking(
                callback=confirm_cb,
                state=state,
                notifier=SimpleNamespace(maybe_send=AsyncMock(return_value=True), policy=DefaultNotificationPolicy()),
            )

        self.assertEqual(await state.get_data(), {})

    async def test_confirm_pro_enqueues_client_notification(self) -> None:
        from src.handlers.master import add_booking as h

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(),
            answer=AsyncMock(),
        )
        state = MemoryState()

        now = datetime.now(UTC)
        confirm = SimpleNamespace(
            client={"telegram_id": 123, "notifications_enabled": True},
            slot_dt=now + timedelta(days=1),
        )
        result = SimpleNamespace(
            booking=SimpleNamespace(id=7),
            master=SimpleNamespace(notify_clients=True),
            warn_master_bookings_near_limit=False,
            usage=None,
            bookings_limit=None,
            plan_is_pro=True,
        )
        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True), policy=DefaultNotificationPolicy())
        outbox = SimpleNamespace(enqueue_booking_client_notification=AsyncMock(return_value=1))

        with (
            patch.object(h, "cleanup_messages", AsyncMock()),
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "ScheduledNotificationRepository", lambda _s: outbox),
        ):
            await h._handle_success(callback, state, notifier, confirm=confirm, result=result)

        outbox.enqueue_booking_client_notification.assert_awaited()

    async def test_confirm_free_plan_does_not_enqueue_client_notification(self) -> None:
        from src.handlers.master import add_booking as h

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(),
            answer=AsyncMock(),
        )
        state = MemoryState()

        now = datetime.now(UTC)
        confirm = SimpleNamespace(
            client={"telegram_id": 123, "notifications_enabled": True},
            slot_dt=now + timedelta(days=1),
        )
        result = SimpleNamespace(
            booking=SimpleNamespace(id=7),
            master=SimpleNamespace(notify_clients=True),
            warn_master_bookings_near_limit=False,
            usage=None,
            bookings_limit=None,
            plan_is_pro=False,
        )
        notifier = SimpleNamespace(maybe_send=AsyncMock(return_value=True), policy=DefaultNotificationPolicy())
        outbox = SimpleNamespace(enqueue_booking_client_notification=AsyncMock(return_value=1))

        with (
            patch.object(h, "cleanup_messages", AsyncMock()),
            patch.object(h, "active_session", _fake_active_session),
            patch.object(h, "ScheduledNotificationRepository", lambda _s: outbox),
        ):
            await h._handle_success(callback, state, notifier, confirm=confirm, result=result)

        outbox.enqueue_booking_client_notification.assert_not_awaited()
