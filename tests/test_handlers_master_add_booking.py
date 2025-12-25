from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        callback_data = SimpleNamespace()

        class _Calendar:
            async def process_selection(self, callback, callback_data):
                return True, datetime.now(UTC)

            async def start_calendar(self):
                return SimpleNamespace()

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

        with (
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "SimpleCalendar", _Calendar),
            patch.object(h, "session_local", _fake_session_local),
            patch.object(h, "EntitlementsService", _Entitlements),
            patch.object(h, "GetMasterFreeSlots", _Slots),
        ):
            await h.pick_date(callback=callback, callback_data=callback_data, state=state)

        callback.answer.assert_awaited()
        callback.message.edit_text.assert_awaited()

    async def test_pick_date_out_of_range_restores_calendar(self) -> None:
        from src.handlers.master import add_booking as h

        state = MemoryState()
        await state.update_data(
            master_id=1,
            master_timezone="Europe/Minsk",
        )

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        callback_data = SimpleNamespace()

        class _Calendar:
            async def process_selection(self, callback, callback_data):
                return True, datetime.now(UTC)

            async def start_calendar(self):
                return SimpleNamespace()

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

        with (
            patch.object(h, "track_callback_message", AsyncMock()),
            patch.object(h, "SimpleCalendar", _Calendar),
            patch.object(h, "session_local", _fake_session_local),
            patch.object(h, "EntitlementsService", _Entitlements),
            patch.object(h, "GetMasterFreeSlots", _Slots),
        ):
            await h.pick_date(callback=callback, callback_data=callback_data, state=state)

        callback.answer.assert_awaited()
        callback.message.edit_text.assert_awaited()

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
                notifier=SimpleNamespace(maybe_send=AsyncMock(return_value=False)),
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
                notifier=SimpleNamespace(maybe_send=AsyncMock()),
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

        slot_utc = datetime.now(UTC) + timedelta(days=1)

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
            patch.object(h, "answer_tracked", AsyncMock()),
            patch.object(h, "_load_master_with_clients", AsyncMock(return_value=master)),
            patch.object(h, "SimpleCalendar", _Calendar),
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
                data="",
                bot=bot,
                message=SimpleNamespace(edit_text=AsyncMock()),
                answer=AsyncMock(),
            )
            await h.pick_date(callback=pick_date_cb, callback_data=SimpleNamespace(), state=state)
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
                notifier=SimpleNamespace(maybe_send=AsyncMock(return_value=True)),
            )

        self.assertEqual(await state.get_data(), {})
