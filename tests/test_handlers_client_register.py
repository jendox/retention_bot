from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.notifications import NotificationEvent, RecipientKind
from src.notifications.notifier import NotificationRequest
from src.use_cases.accept_client_invite import (
    AcceptClientInviteResult,
    AcceptInviteError,
    AcceptInviteOutcome,
)
from src.use_cases.entitlements import Usage
from src.user_context import ActiveRole


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


class RegisterHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_error_message_none_is_noop(self) -> None:
        from src.handlers.client import register as reg

        bot = SimpleNamespace(send_message=AsyncMock())
        await reg._send_error_message(bot=bot, chat_id=1, error=None)
        bot.send_message.assert_not_awaited()

    async def test_send_error_message_maps_errors(self) -> None:
        from src.handlers.client import register as reg

        bot = SimpleNamespace(send_message=AsyncMock())
        await reg._send_error_message(bot=bot, chat_id=1, error=AcceptInviteError.INVITE_INVALID)
        await reg._send_error_message(bot=bot, chat_id=1, error=AcceptInviteError.INVITE_WRONG_TYPE)
        await reg._send_error_message(bot=bot, chat_id=1, error=AcceptInviteError.QUOTA_EXCEEDED)
        await reg._send_error_message(bot=bot, chat_id=1, error=AcceptInviteError.PHONE_CONFLICT)
        await reg._send_error_message(bot=bot, chat_id=1, error=AcceptInviteError.MISSING_PHONE)
        self.assertGreaterEqual(bot.send_message.await_count, 5)

    async def test_start_client_registration_ok_sets_role_and_sends_menu(self) -> None:
        from src.handlers.client import register as reg

        state = MemoryState()
        user_ctx_storage = SimpleNamespace(set_role=AsyncMock())

        message = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(),
            message_id=1,
        )

        result = AcceptClientInviteResult(ok=True, outcome=AcceptInviteOutcome.CREATED, master_id=1, client_id=2)

        captured = SimpleNamespace(request=None)

        class _UC:
            def __init__(self, session) -> None:
                pass

            async def execute(self, request):
                captured.request = request
                return result

        send_menu = AsyncMock()
        with (
            patch.object(reg, "track_message", AsyncMock()),
            patch.object(reg, "active_session", _fake_active_session),
            patch.object(reg, "AcceptClientInvite", _UC),
            patch.object(reg, "_check_if_master", AsyncMock(return_value=True)),
            patch.object(reg, "send_client_main_menu", send_menu),
        ):
            await reg.start_client_registration(
                message=message,
                state=state,
                user_ctx_storage=user_ctx_storage,
                invite_link="c_token123",
            )

        user_ctx_storage.set_role.assert_awaited_with(10, ActiveRole.CLIENT)
        send_menu.assert_awaited()
        self.assertIsNotNone(captured.request)
        self.assertEqual(captured.request.invite_token, "token123")

    async def test_start_client_registration_missing_phone_starts_fsm(self) -> None:
        from src.handlers.client import register as reg

        state = MemoryState()
        user_ctx_storage = SimpleNamespace(set_role=AsyncMock())

        message = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(),
            message_id=1,
        )

        result = AcceptClientInviteResult(
            ok=False,
            error=AcceptInviteError.MISSING_PHONE,
            master_id=123,
            master_telegram_id=777,
        )

        class _UC:
            def __init__(self, session) -> None:
                pass

            async def execute(self, request):
                self.request = request
                return result

        process_name = AsyncMock()
        with (
            patch.object(reg, "track_message", AsyncMock()),
            patch.object(reg, "active_session", _fake_active_session),
            patch.object(reg, "AcceptClientInvite", _UC),
            patch.object(reg, "process_name_question", process_name),
        ):
            await reg.start_client_registration(
                message=message,
                state=state,
                user_ctx_storage=user_ctx_storage,
                invite_link="c_token123",
            )

        data = await state.get_data()
        self.assertEqual(data["invite_data"]["invite_master_id"], 123)
        self.assertEqual(data["invite_data"]["invite_token"], "token123")
        process_name.assert_awaited()

    async def test_start_client_registration_other_error_sends_error_and_cleans_up(self) -> None:
        from src.handlers.client import register as reg

        state = MemoryState()
        user_ctx_storage = SimpleNamespace(set_role=AsyncMock())

        message = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=SimpleNamespace(),
            message_id=1,
        )

        result = AcceptClientInviteResult(ok=False, error=AcceptInviteError.INVITE_INVALID, master_id=123)

        class _UC:
            def __init__(self, session) -> None:
                pass

            async def execute(self, request):
                return result

        send_error = AsyncMock()
        cleanup = AsyncMock()
        with (
            patch.object(reg, "track_message", AsyncMock()),
            patch.object(reg, "active_session", _fake_active_session),
            patch.object(reg, "AcceptClientInvite", _UC),
            patch.object(reg, "_send_error_message", send_error),
            patch.object(reg, "cleanup_messages", cleanup),
        ):
            await reg.start_client_registration(
                message=message,
                state=state,
                user_ctx_storage=user_ctx_storage,
                invite_link="c_token123",
            )

        send_error.assert_awaited()
        cleanup.assert_awaited()

    async def test_process_client_name_empty_reprompts(self) -> None:
        from src.handlers.client import register as reg

        state = MemoryState()
        message = SimpleNamespace(text="   ", message_id=1, chat=SimpleNamespace(id=10))

        answer = AsyncMock()
        with (
            patch.object(reg, "track_message", AsyncMock()),
            patch.object(reg, "answer_tracked", answer),
        ):
            await reg.process_client_name(message=message, state=state)

        answer.assert_awaited()
        data = await state.get_data()
        self.assertNotIn("name", data)

    async def test_process_client_name_valid_advances_state(self) -> None:
        from src.handlers.client import register as reg

        state = MemoryState()
        message = SimpleNamespace(text=" Маша ", message_id=1, chat=SimpleNamespace(id=10))

        answer = AsyncMock()
        with (
            patch.object(reg, "track_message", AsyncMock()),
            patch.object(reg, "answer_tracked", answer),
        ):
            await reg.process_client_name(message=message, state=state)

        data = await state.get_data()
        self.assertEqual(data["name"], "Маша")
        answer.assert_awaited()

    async def test_process_client_phone_invalid_reprompts(self) -> None:
        from src.handlers.client import register as reg

        state = MemoryState()
        await state.update_data(name="Маша")
        message = SimpleNamespace(text="bad", message_id=1, chat=SimpleNamespace(id=10))

        answer = AsyncMock()
        with (
            patch.object(reg, "track_message", AsyncMock()),
            patch.object(reg, "validate_phone", lambda _: None),
            patch.object(reg, "answer_tracked", answer),
        ):
            await reg.process_client_phone(message=message, state=state)

        answer.assert_awaited()
        data = await state.get_data()
        self.assertNotIn("phone", data)

    async def test_process_client_phone_valid_shows_confirmation(self) -> None:
        from src.handlers.client import register as reg

        state = MemoryState()
        await state.update_data(name="Маша")
        message = SimpleNamespace(text="375291234567", message_id=1, chat=SimpleNamespace(id=10))

        answer = AsyncMock()
        with (
            patch.object(reg, "track_message", AsyncMock()),
            patch.object(reg, "validate_phone", lambda _: "+375291234567"),
            patch.object(reg, "answer_tracked", answer),
        ):
            await reg.process_client_phone(message=message, state=state)

        data = await state.get_data()
        self.assertEqual(data["phone"], "+375291234567")
        answer.assert_awaited()

    async def test_client_reg_confirm_validation_error_clears_state(self) -> None:
        from src.handlers.client import register as reg

        state = MemoryState()
        await state.update_data(name="N", phone="+375291234567", invite_data=None)

        bot = SimpleNamespace(send_message=AsyncMock())
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=bot,
            message=SimpleNamespace(edit_reply_markup=AsyncMock()),
            answer=AsyncMock(),
        )

        cleanup = AsyncMock()
        with (
            patch.object(reg, "track_callback_message", AsyncMock()),
            patch.object(reg, "answer_tracked", AsyncMock()),
            patch.object(reg, "cleanup_messages", cleanup),
        ):
            await reg.client_reg_confirm(
                callback=callback,
                state=state,
                user_ctx_storage=SimpleNamespace(set_role=AsyncMock()),
                notifier=SimpleNamespace(maybe_send=AsyncMock()),
            )

        callback.answer.assert_awaited()
        cleanup.assert_awaited()
        data = await state.get_data()
        self.assertEqual(data, {})

    async def test_client_reg_confirm_success_sends_warning_notification(self) -> None:
        from src.handlers.client import register as reg

        state = MemoryState()
        await state.update_data(
            name="N",
            phone="+375291234567",
            invite_data={"invite_master_id": 123, "invite_token": "t"},
        )
        user_ctx_storage = SimpleNamespace(set_role=AsyncMock())

        bot = SimpleNamespace(send_message=AsyncMock())
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=bot,
            message=SimpleNamespace(edit_reply_markup=AsyncMock()),
            answer=AsyncMock(),
        )

        result = AcceptClientInviteResult(
            ok=True,
            outcome=AcceptInviteOutcome.CREATED,
            master_id=123,
            master_telegram_id=777,
            client_id=456,
            warn_master_clients_near_limit=True,
            usage=Usage(clients_count=8, bookings_created_this_month=0),
        )

        class _UC:
            def __init__(self, session) -> None:
                pass

            async def execute(self, request):
                self.request = request
                return result

        notifier = SimpleNamespace(maybe_send=AsyncMock())

        send_menu = AsyncMock()
        with (
            patch.object(reg, "track_callback_message", AsyncMock()),
            patch.object(reg, "answer_tracked", AsyncMock()),
            patch.object(reg, "cleanup_messages", AsyncMock()),
            patch.object(reg, "active_session", _fake_active_session),
            patch.object(reg, "AcceptClientInvite", _UC),
            patch.object(reg, "_check_if_master", AsyncMock(return_value=False)),
            patch.object(reg, "send_client_main_menu", send_menu),
        ):
            await reg.client_reg_confirm(
                callback=callback,
                state=state,
                user_ctx_storage=user_ctx_storage,
                notifier=notifier,
            )

        bot.send_message.assert_awaited()
        user_ctx_storage.set_role.assert_awaited_with(10, ActiveRole.CLIENT)
        notifier.maybe_send.assert_awaited()
        sent_req: NotificationRequest = notifier.maybe_send.await_args.args[0]
        self.assertEqual(sent_req.event, NotificationEvent.WARNING_NEAR_CLIENTS_LIMIT)
        self.assertEqual(sent_req.recipient, RecipientKind.MASTER)
        self.assertEqual(sent_req.chat_id, 777)
        send_menu.assert_awaited()

    async def test_client_reg_confirm_failure_sends_error_message(self) -> None:
        from src.handlers.client import register as reg

        state = MemoryState()
        await state.update_data(
            name="N",
            phone="+375291234567",
            invite_data={"invite_master_id": 123, "invite_token": "t"},
        )

        bot = SimpleNamespace(send_message=AsyncMock())
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            bot=bot,
            message=SimpleNamespace(edit_reply_markup=AsyncMock()),
            answer=AsyncMock(),
        )

        result = AcceptClientInviteResult(
            ok=False,
            error=AcceptInviteError.QUOTA_EXCEEDED,
            master_id=123,
            master_telegram_id=777,
        )

        class _UC:
            def __init__(self, session) -> None:
                pass

            async def execute(self, request):
                return result

        send_error = AsyncMock()
        with (
            patch.object(reg, "track_callback_message", AsyncMock()),
            patch.object(reg, "answer_tracked", AsyncMock()),
            patch.object(reg, "cleanup_messages", AsyncMock()),
            patch.object(reg, "active_session", _fake_active_session),
            patch.object(reg, "AcceptClientInvite", _UC),
            patch.object(reg, "_send_error_message", send_error),
        ):
            await reg.client_reg_confirm(
                callback=callback,
                state=state,
                user_ctx_storage=SimpleNamespace(set_role=AsyncMock()),
                notifier=SimpleNamespace(maybe_send=AsyncMock()),
            )

        send_error.assert_awaited_with(bot, 10, AcceptInviteError.QUOTA_EXCEEDED)

    async def test_client_reg_restart_resets_state_and_reasks_name(self) -> None:
        from src.handlers.client import register as reg

        state = MemoryState()
        await state.update_data(invite_data={"invite_master_id": 123, "invite_token": "t"})

        callback = SimpleNamespace(
            message=SimpleNamespace(),
            bot=SimpleNamespace(),
            answer=AsyncMock(),
        )

        process_name = AsyncMock()
        with (
            patch.object(reg, "track_callback_message", AsyncMock()),
            patch.object(reg, "cleanup_messages", AsyncMock()),
            patch.object(reg, "process_name_question", process_name),
        ):
            await reg.client_reg_restart(callback=callback, state=state)

        callback.answer.assert_awaited()
        process_name.assert_awaited()
        data = await state.get_data()
        self.assertIn("invite_data", data)

    async def test_client_reg_cancel_cleans_up_and_clears(self) -> None:
        from src.handlers.client import register as reg

        state = MemoryState()
        callback = SimpleNamespace(
            bot=SimpleNamespace(),
            answer=AsyncMock(),
        )

        cleanup = AsyncMock()
        with (
            patch.object(reg, "cleanup_messages", cleanup),
        ):
            await reg.client_reg_cancel(callback=callback, state=state)

        callback.answer.assert_awaited()
        cleanup.assert_awaited()
        data = await state.get_data()
        self.assertEqual(data, {})
