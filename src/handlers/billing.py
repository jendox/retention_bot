from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from src.core.sa import active_session
from src.filters.user_role import UserRole
from src.integrations.expresspay import CurrencyCode, ExpressPayClient
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.paywall import build_upgrade_button_with_fallback
from src.repositories.payment_invoice import PaymentInvoiceRepository
from src.settings import get_settings
from src.texts import billing as txt
from src.use_cases.check_pro_payment import CheckProPayment, CheckProPaymentError, CheckProPaymentRequest
from src.use_cases.create_pro_invoice import CreateProInvoice, CreateProInvoiceError, CreateProInvoiceRequest
from src.use_cases.create_pro_renewal_invoice import (
    CreateProRenewalInvoice,
    CreateProRenewalInvoiceError,
    CreateProRenewalInvoiceRequest,
)
from src.user_context import ActiveRole

router = Router(name=__name__)
ev = EventLogger(__name__)


_START_CB = "billing:pro:start"
_RENEW_CB = "billing:pro:renew"
_CHECK_PREFIX = "billing:pro:check:"
_NEW_CB = "billing:pro:new"


def _kb_invoice(*, invoice_url: str | None, invoice_id: int, contact: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if invoice_url:
        rows.append([InlineKeyboardButton(text=txt.btn_pay(), url=invoice_url)])
    else:
        rows.append([InlineKeyboardButton(text=txt.btn_pay(), callback_data=_NEW_CB)])
    rows.append([InlineKeyboardButton(text=txt.btn_check(), callback_data=f"{_CHECK_PREFIX}{invoice_id}")])
    rows.append(
        [
            build_upgrade_button_with_fallback(
                contact=contact,
                text=txt.btn_contact(),
                callback_data="paywall:contact",
            ),
        ],
    )
    rows.append([InlineKeyboardButton(text=txt.btn_close(), callback_data="paywall:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _parse_invoice_id(data: str) -> int | None:
    if not data.startswith(_CHECK_PREFIX):
        return None
    raw = data.removeprefix(_CHECK_PREFIX)
    try:
        invoice_id = int(raw)
    except ValueError:
        return None
    return invoice_id if invoice_id > 0 else None


def _kb_config_missing(*, contact: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                build_upgrade_button_with_fallback(
                    contact=contact,
                    text=txt.btn_contact(),
                    callback_data="paywall:contact",
                ),
            ],
            [InlineKeyboardButton(text=txt.btn_close(), callback_data="paywall:close")],
        ],
    )


def _kb_retry_new_invoice(*, contact: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=txt.btn_new_invoice(), callback_data=_NEW_CB)],
            [
                build_upgrade_button_with_fallback(
                    contact=contact,
                    text=txt.btn_contact(),
                    callback_data="paywall:contact",
                ),
            ],
            [InlineKeyboardButton(text=txt.btn_close(), callback_data="paywall:close")],
        ],
    )


def _kb_paid(*, contact: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=txt.btn_close(), callback_data="paywall:close")],
            [
                build_upgrade_button_with_fallback(
                    contact=contact,
                    text=txt.btn_contact(),
                    callback_data="paywall:contact",
                ),
            ],
        ],
    )


async def _answer_with_contact(callback: CallbackQuery, *, contact: str) -> None:
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=txt.contact_message(contact=contact),
        parse_mode="HTML",
        reply_markup=_kb_config_missing(contact=contact),
    )


def _check_result_to_response(
    result,
    *,
    contact: str,
) -> tuple[str, str | None, str | None, bool]:
    """
    Returns a tuple: (kind, text, parse_mode, show_alert).
    kind: "contact" | "message" | "alert"
    """
    kind = "alert"
    text: str | None = txt.pro_still_waiting()
    parse_mode: str | None = None
    show_alert = True

    if not result.ok:
        kind = "message"
        text = txt.pro_error()
        show_alert = False
        if result.error in {CheckProPaymentError.FORBIDDEN, CheckProPaymentError.INVOICE_NOT_FOUND}:
            kind = "contact"
            text = None
    else:
        invoice = result.invoice
        if invoice is None:
            kind = "message"
            text = txt.pro_error()
            show_alert = False
        elif result.granted_pro:
            kind = "message"
            text = txt.pro_paid()
            parse_mode = "HTML"
            show_alert = False
        else:
            status = getattr(invoice, "status", None)
            status_value = str(status.value) if status is not None else ""
            if status_value == "expired":
                kind = "message"
                text = txt.pro_expired()
                show_alert = False
            elif status_value == "canceled":
                kind = "message"
                text = txt.pro_canceled()
                show_alert = False

    return kind, text, parse_mode, show_alert


async def _require_expresspay_and_days(
    callback: CallbackQuery,
    *,
    express_pay_client: ExpressPayClient | None,
) -> tuple[ExpressPayClient, int, str] | None:
    settings = get_settings()
    contact = settings.billing.contact
    days = settings.billing.pro_days

    if express_pay_client is None:
        ev.info("billing.pro_check_unavailable", reason="no_express_pay_client")
        await callback.answer()
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=txt.pro_config_missing(),
            reply_markup=_kb_config_missing(contact=contact),
            parse_mode="HTML",
        )
        return None
    if days is None:
        ev.info("billing.pro_check_unavailable", reason="billing_config_missing")
        await callback.answer()
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=txt.pro_config_missing(),
            reply_markup=_kb_config_missing(contact=contact),
            parse_mode="HTML",
        )
        return None
    return express_pay_client, int(days), contact


async def _create_and_show_invoice(
    callback: CallbackQuery,
    *,
    express_pay_client: ExpressPayClient,
    reuse_waiting: bool,
) -> None:
    settings = get_settings()
    contact = settings.billing.contact

    price = settings.billing.pro_price_byn
    days = settings.billing.pro_days
    if price is None or days is None:
        ev.info("billing.pro_start_unavailable", reason="billing_config_missing")
        await callback.answer()
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=txt.pro_config_missing(),
            reply_markup=_kb_config_missing(contact=contact),
            parse_mode="HTML",
        )
        return
    price_dec = Decimal(price)

    async with active_session() as session:
        use_case = CreateProInvoice(session, express_pay_client=express_pay_client)
        result = await use_case.execute(
            CreateProInvoiceRequest(
                master_telegram_id=callback.from_user.id,
                amount=price_dec,
                currency=CurrencyCode.BYN,
                description=settings.billing.pro_description,
                lifetime_seconds=settings.billing.pro_invoice_lifetime_sec,
                reuse_waiting=reuse_waiting,
            ),
        )

    await callback.answer()
    if not result.ok:
        ev.info(
            "billing.pro_start_failed",
            error=str(result.error.value) if result.error else None,
        )
        if result.error == CreateProInvoiceError.ALREADY_PRO:
            await callback.answer(txt.pro_already_active(), show_alert=True)
            return
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=txt.pro_error(),
            reply_markup=_kb_config_missing(contact=contact),
            parse_mode="HTML",
        )
        return

    assert result.invoice is not None
    invoice = result.invoice
    ev.info("billing.pro_invoice_ready", invoice_id=invoice.id, reused=bool(result.reused))

    message_text = txt.pro_invoice_created(days=int(days), price_byn=float(price_dec))
    markup = _kb_invoice(invoice_url=result.invoice_url, invoice_id=int(invoice.id), contact=contact)

    if callback.message is not None:
        try:
            await callback.message.edit_text(message_text, reply_markup=markup, parse_mode="HTML")
            return
        except Exception:
            pass
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=message_text,
        reply_markup=markup,
        parse_mode="HTML",
    )


async def _create_and_show_renewal_invoice(
    callback: CallbackQuery,
    *,
    express_pay_client: ExpressPayClient,
    reuse_waiting: bool,
) -> None:
    settings = get_settings()
    contact = settings.billing.contact

    price = settings.billing.pro_price_byn
    days = settings.billing.pro_days
    if price is None or days is None:
        ev.info("billing.pro_renew_unavailable", reason="billing_config_missing")
        await callback.answer()
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=txt.pro_config_missing(),
            reply_markup=_kb_config_missing(contact=contact),
            parse_mode="HTML",
        )
        return
    price_dec = Decimal(price)

    async with active_session() as session:
        use_case = CreateProRenewalInvoice(session, express_pay_client=express_pay_client)
        result = await use_case.execute(
            CreateProRenewalInvoiceRequest(
                master_telegram_id=callback.from_user.id,
                amount=price_dec,
                currency=CurrencyCode.BYN,
                description=settings.billing.pro_description,
                lifetime_seconds=settings.billing.pro_invoice_lifetime_sec,
                reuse_waiting=reuse_waiting,
            ),
        )

    await callback.answer()
    if not result.ok:
        ev.info(
            "billing.pro_renew_failed",
            error=str(result.error.value) if result.error else None,
        )
        if result.error in {CreateProRenewalInvoiceError.MASTER_NOT_FOUND, CreateProRenewalInvoiceError.PROVIDER_ERROR}:
            await callback.bot.send_message(
                chat_id=callback.from_user.id,
                text=txt.pro_error(),
                reply_markup=_kb_config_missing(contact=contact),
                parse_mode="HTML",
            )
            return
        await _answer_with_contact(callback, contact=contact)
        return

    assert result.invoice is not None
    invoice = result.invoice
    ev.info("billing.pro_renewal_invoice_ready", invoice_id=invoice.id, reused=bool(result.reused))

    message_text = txt.pro_invoice_created(days=int(days), price_byn=float(price_dec))
    markup = _kb_invoice(invoice_url=result.invoice_url, invoice_id=int(invoice.id), contact=contact)

    if callback.message is not None:
        try:
            await callback.message.edit_text(message_text, reply_markup=markup, parse_mode="HTML")
            return
        except Exception:
            pass
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=message_text,
        reply_markup=markup,
        parse_mode="HTML",
    )


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == _START_CB)
async def billing_pro_start(
    callback: CallbackQuery,
    express_pay_client: ExpressPayClient | None = None,
) -> None:
    bind_log_context(flow="billing_pro", step="start")
    settings = get_settings()
    contact = settings.billing.contact

    if express_pay_client is None:
        ev.info("billing.pro_start_unavailable", reason="no_express_pay_client")
        await callback.answer()
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=txt.pro_config_missing(),
            reply_markup=_kb_config_missing(contact=contact),
            parse_mode="HTML",
        )
        return
    await _create_and_show_invoice(callback, express_pay_client=express_pay_client, reuse_waiting=True)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == _RENEW_CB)
async def billing_pro_renew(
    callback: CallbackQuery,
    express_pay_client: ExpressPayClient | None = None,
) -> None:
    bind_log_context(flow="billing_pro", step="renew")
    settings = get_settings()
    contact = settings.billing.contact

    if express_pay_client is None:
        ev.info("billing.pro_renew_unavailable", reason="no_express_pay_client")
        await callback.answer()
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=txt.pro_config_missing(),
            reply_markup=_kb_config_missing(contact=contact),
            parse_mode="HTML",
        )
        return
    await _create_and_show_renewal_invoice(callback, express_pay_client=express_pay_client, reuse_waiting=True)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == _NEW_CB)
async def billing_pro_new_invoice(
    callback: CallbackQuery,
    express_pay_client: ExpressPayClient | None = None,
) -> None:
    bind_log_context(flow="billing_pro", step="new_invoice")
    settings = get_settings()
    contact = settings.billing.contact

    if express_pay_client is None:
        ev.info("billing.pro_start_unavailable", reason="no_express_pay_client")
        await callback.answer()
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=txt.pro_config_missing(),
            reply_markup=_kb_config_missing(contact=contact),
            parse_mode="HTML",
        )
        return
    await _create_and_show_renewal_invoice(callback, express_pay_client=express_pay_client, reuse_waiting=False)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith(_CHECK_PREFIX))
async def billing_pro_check(  # noqa: C901, PLR0912
    callback: CallbackQuery,
    express_pay_client: ExpressPayClient | None = None,
) -> None:
    bind_log_context(flow="billing_pro", step="check")
    invoice_id = _parse_invoice_id(callback.data or "")
    if invoice_id is None:
        await callback.answer()
        return
    prereq = await _require_expresspay_and_days(callback, express_pay_client=express_pay_client)
    if prereq is None:
        return
    express_pay_client, days, contact = prereq

    async with active_session() as session:
        use_case = CheckProPayment(session, express_pay_client=express_pay_client)
        result = await use_case.execute(
            CheckProPaymentRequest(
                master_telegram_id=callback.from_user.id,
                invoice_id=invoice_id,
                pro_days=int(days),
            ),
        )

    if not result.ok:
        ev.info("billing.pro_check_failed", error=str(result.error.value) if result.error else None)

    if result.ok and result.invoice is not None and result.invoice.status.value == "paid":
        if result.invoice.paid_notified_at is None:
            await callback.answer(txt.pro_paid_alert(paid_until=result.paid_until), show_alert=True)
        else:
            await callback.answer("Оплата уже учтена ✅", show_alert=True)

        if callback.message is not None:
            try:
                await callback.message.edit_reply_markup(reply_markup=_kb_paid(contact=contact))
            except Exception:
                pass
        if result.invoice.paid_notified_at is None:
            await callback.bot.send_message(
                chat_id=callback.from_user.id,
                text=txt.pro_paid_message(paid_until=result.paid_until),
                parse_mode="HTML",
            )
            async with active_session() as session:
                await PaymentInvoiceRepository(session).mark_paid_notified(
                    int(result.invoice.id),
                    at=datetime.now(UTC),
                )
        return

    kind, text, parse_mode, show_alert = _check_result_to_response(result, contact=contact)
    if kind == "contact":
        await callback.answer()
        await _answer_with_contact(callback, contact=contact)
        return

    if kind == "message" and text is not None:
        await callback.answer()
        if text in {txt.pro_expired(), txt.pro_canceled()}:
            await callback.bot.send_message(
                chat_id=callback.from_user.id,
                text=text,
                reply_markup=_kb_retry_new_invoice(contact=contact),
                parse_mode=parse_mode,
            )
            return
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=text,
            parse_mode=parse_mode,
        )
        return

    if kind == "alert" and text is not None:
        await callback.answer(text, show_alert=show_alert)
