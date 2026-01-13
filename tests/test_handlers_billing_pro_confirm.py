from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class BillingProConfirmationFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_billing_pro_start_sends_confirmation_in_generic_context(self) -> None:
        from src.handlers import billing as b

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=123),
            message=SimpleNamespace(text="not tariffs"),
            bot=SimpleNamespace(send_message=AsyncMock()),
            answer=AsyncMock(),
        )

        with patch.object(b, "_create_and_show_invoice", AsyncMock()) as create_and_show:
            await b.billing_pro_start(callback, express_pay_client=SimpleNamespace())

        create_and_show.assert_not_awaited()
        callback.bot.send_message.assert_awaited_once()
        kwargs = callback.bot.send_message.await_args.kwargs
        markup = kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "billing:pro:confirm_generic:start")

    async def test_billing_pro_renew_sends_confirmation_in_generic_context(self) -> None:
        from src.handlers import billing as b

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=123),
            message=SimpleNamespace(text="not tariffs"),
            bot=SimpleNamespace(send_message=AsyncMock()),
            answer=AsyncMock(),
        )

        with patch.object(b, "_create_and_show_renewal_invoice", AsyncMock()) as create_and_show:
            await b.billing_pro_renew(callback, express_pay_client=SimpleNamespace())

        create_and_show.assert_not_awaited()
        callback.bot.send_message.assert_awaited_once()
        kwargs = callback.bot.send_message.await_args.kwargs
        markup = kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "billing:pro:confirm_generic:renew")

    async def test_billing_pro_confirm_generic_cancel_deletes_message(self) -> None:
        from src.handlers import billing as b

        msg = SimpleNamespace(delete=AsyncMock())
        callback = SimpleNamespace(
            message=msg,
            answer=AsyncMock(),
        )

        await b.billing_pro_confirm_generic_cancel(callback)
        msg.delete.assert_awaited_once()

    async def test_billing_pro_confirm_generic_yes_start_calls_create_and_show(self) -> None:
        from src.handlers import billing as b

        callback = SimpleNamespace(
            data="billing:pro:confirm_generic:start",
            from_user=SimpleNamespace(id=123),
            message=SimpleNamespace(),
            bot=SimpleNamespace(send_message=AsyncMock()),
            answer=AsyncMock(),
        )

        with patch.object(b, "_create_and_show_invoice", AsyncMock()) as create_and_show:
            await b.billing_pro_confirm_generic_yes(callback, express_pay_client=SimpleNamespace())

        create_and_show.assert_awaited_once()

    async def test_billing_pro_confirm_generic_yes_renew_calls_create_and_show(self) -> None:
        from src.handlers import billing as b

        callback = SimpleNamespace(
            data="billing:pro:confirm_generic:renew",
            from_user=SimpleNamespace(id=123),
            message=SimpleNamespace(),
            bot=SimpleNamespace(send_message=AsyncMock()),
            answer=AsyncMock(),
        )

        with patch.object(b, "_create_and_show_renewal_invoice", AsyncMock()) as create_and_show:
            await b.billing_pro_confirm_generic_yes(callback, express_pay_client=SimpleNamespace())

        create_and_show.assert_awaited_once()
