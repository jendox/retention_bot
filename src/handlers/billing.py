from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from src.core.sa import active_session
from src.filters.user_role import UserRole
from src.integrations.expresspay import CurrencyCode, ExpressPayClient
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.paywall import build_paywall_keyboard, build_upgrade_button_with_fallback
from src.repositories.master import MasterNotFound, MasterRepository
from src.repositories.payment_invoice import PaymentInvoiceRepository
from src.settings import get_settings
from src.texts import billing as txt, paywall as paywall_txt
from src.texts.buttons import btn_back, btn_cancel, btn_go_pro
from src.use_cases.check_pro_payment import CheckProPayment, CheckProPaymentError, CheckProPaymentRequest
from src.use_cases.create_pro_invoice import (
    CreateProInvoice,
    CreateProInvoiceError,
    CreateProInvoiceRequest,
    CreateProInvoiceResult,
)
from src.use_cases.create_pro_renewal_invoice import (
    CreateProRenewalInvoice,
    CreateProRenewalInvoiceError,
    CreateProRenewalInvoiceRequest,
    CreateProRenewalInvoiceResult,
)
from src.use_cases.entitlements import EntitlementsService
from src.user_context import ActiveRole

router = Router(name=__name__)
ev = EventLogger(__name__)


_START_CB = "billing:pro:start"
_RENEW_CB = "billing:pro:renew"
_CHECK_PREFIX = "billing:pro:check:"
_NEW_CB = "billing:pro:new"

_CONFIRM_PREFIX = "billing:pro:confirm:"
_CONFIRM_CANCEL_PREFIX = "billing:pro:confirm_cancel:"

_CONFIRM_PAYWALL_PREFIX = "billing:pro:confirm_paywall:"
_CONFIRM_PAYWALL_CANCEL_PREFIX = "billing:pro:confirm_paywall_cancel:"

_MASTER_SETTINGS_BACK_MENU_CB = "m:settings:back_menu"


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


def _kb_waiting_invoice(*, invoice_url: str | None, invoice_id: int, contact: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if invoice_url:
        rows.append([InlineKeyboardButton(text=txt.btn_pay(), url=invoice_url)])
    rows.append([InlineKeyboardButton(text=txt.btn_check(), callback_data=f"{_CHECK_PREFIX}{int(invoice_id)}")])
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


def _kb_confirm_invoice(*, action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data=f"{_CONFIRM_PREFIX}{action}"),
                InlineKeyboardButton(text=btn_cancel(), callback_data=f"{_CONFIRM_CANCEL_PREFIX}{action}"),
            ],
        ],
    )


def _is_tariffs_message(callback: CallbackQuery) -> bool:
    """
    We can only "return to previous step" safely when the action was triggered
    from the master tariffs screen (single-message UX).
    """
    msg = callback.message
    if msg is None:
        return False
    text = getattr(msg, "html_text", None) or getattr(msg, "text", None) or ""
    return "<b>Тарифы</b>" in str(text)


def _is_clients_limit_paywall_message(callback: CallbackQuery) -> bool:
    msg = callback.message
    if msg is None:
        return False
    text = getattr(msg, "html_text", None) or getattr(msg, "text", None) or ""
    raw = str(text)
    return ("Лимит Free:" in raw) and ("клиент" in raw)


def _parse_clients_limit_from_paywall(callback: CallbackQuery) -> int | None:
    msg = callback.message
    if msg is None:
        return None
    text = getattr(msg, "html_text", None) or getattr(msg, "text", None) or ""
    import re

    match = re.search(r"Лимит Free:\s*(\d+)", str(text))
    if match is None:
        return None
    try:
        value = int(match.group(1))
    except ValueError:
        return None
    return value if value > 0 else None


def _kb_confirm_paywall_invoice(*, limit: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да",
                    callback_data=f"{_CONFIRM_PAYWALL_PREFIX}{int(limit)}",
                ),
                InlineKeyboardButton(
                    text=btn_cancel(),
                    callback_data=f"{_CONFIRM_PAYWALL_CANCEL_PREFIX}{int(limit)}",
                ),
            ],
        ],
    )


async def _restore_clients_limit_paywall(callback: CallbackQuery, *, limit: int) -> None:
    if callback.message is None:
        return
    contact = get_settings().billing.contact
    await callback.message.edit_text(
        paywall_txt.clients_limit_reached(limit=int(limit)),
        reply_markup=build_paywall_keyboard(
            contact=contact,
            upgrade_text=btn_go_pro(),
            back_text=btn_back(),
            back_callback_data="paywall:back:clients_menu",
            upgrade_callback_data=_START_CB,
            force_upgrade_callback=True,
        ),
        parse_mode="HTML",
    )


def _plan_label_for_tariffs(*, source: str, is_pro: bool) -> str:
    if source == "trial":
        return "Pro (trial)"
    if source == "paid":
        return "Pro (paid)"
    return "Pro" if is_pro else "Free"


def _tariffs_primary_cb(*, source: str) -> str:
    return _START_CB if source == "free" else _RENEW_CB


async def _load_waiting_invoice(*, repo: PaymentInvoiceRepository, master_id: int) -> object | None:
    invoice = await repo.get_latest_waiting_for_master(master_id=int(master_id))
    if invoice is None:
        return None
    if invoice.expires_at is not None and invoice.expires_at <= datetime.now(UTC):
        return None
    return invoice


async def _load_tariffs_state(callback: CallbackQuery) -> tuple[str, InlineKeyboardMarkup] | None:
    data = await _fetch_tariffs_data(callback)
    if data is None:
        return None
    if data.waiting is not None:
        return _render_tariffs_with_waiting(data)
    return _render_tariffs_base(data)


@dataclass(frozen=True)
class _TariffsInvoiceRef:
    invoice_id: int | None
    invoice_url: str | None


@dataclass(frozen=True)
class _TariffsData:
    contact: str
    source: str
    message_text: str
    primary_text: str | None
    waiting: _TariffsInvoiceRef | None


async def _fetch_tariffs_data(callback: CallbackQuery) -> _TariffsData | None:
    if callback.message is None:
        return None

    settings = get_settings()
    contact = settings.billing.contact
    price = settings.billing.pro_price_byn
    days = settings.billing.pro_days
    primary_text = None if (price is None or days is None) else txt.tariffs_primary_button(
        source="free",
        pro_days=int(days),
        pro_price_byn=float(price),
    )

    async with active_session() as session:
        try:
            master = await MasterRepository(session).get_by_telegram_id(callback.from_user.id)
        except MasterNotFound:
            await callback.answer("Не удалось загрузить профиль мастера.", show_alert=True)
            return None

        plan = await EntitlementsService(session).get_plan(master_id=int(master.id))
        source = str(plan.source)
        plan_label = _plan_label_for_tariffs(source=source, is_pro=bool(plan.is_pro))

        message_text = txt.tariffs_message(
            plan_label=plan_label,
            source=source,
            active_until=plan.active_until,
            pro_days=int(days) if days is not None else None,
            pro_price_byn=float(price) if price is not None else None,
        )

        waiting = await PaymentInvoiceRepository(session).get_latest_waiting_for_master(master_id=int(master.id))
        if waiting is not None and waiting.expires_at is not None and waiting.expires_at <= datetime.now(UTC):
            waiting = None

    waiting_ref = None
    if waiting is not None:
        waiting_ref = _TariffsInvoiceRef(
            invoice_id=int(waiting.id) if getattr(waiting, "id", None) is not None else None,
            invoice_url=str(waiting.invoice_url) if getattr(waiting, "invoice_url", None) else None,
        )

    primary_text = None
    if price is not None and days is not None:
        primary_text = txt.tariffs_primary_button(source=source, pro_days=int(days), pro_price_byn=float(price))

    return _TariffsData(
        contact=contact,
        source=source,
        message_text=message_text,
        primary_text=primary_text,
        waiting=waiting_ref,
    )


def _render_tariffs_with_waiting(data: _TariffsData) -> tuple[str, InlineKeyboardMarkup]:
    message_text = f"{data.message_text}\n\n{txt.pro_waiting_invoice_notice()}"
    rows: list[list[InlineKeyboardButton]] = []
    assert data.waiting is not None
    if data.waiting.invoice_url:
        rows.append([InlineKeyboardButton(text=txt.btn_pay(), url=data.waiting.invoice_url)])
    if data.waiting.invoice_id is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text=txt.btn_check(),
                    callback_data=f"{_CHECK_PREFIX}{int(data.waiting.invoice_id)}",
                ),
            ],
        )
    rows.append(
        [
            build_upgrade_button_with_fallback(
                contact=data.contact,
                text=txt.btn_contact(),
                callback_data="paywall:contact",
            ),
        ],
    )
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=_MASTER_SETTINGS_BACK_MENU_CB)])
    rows.append([InlineKeyboardButton(text="✖️ Закрыть", callback_data="m:close")])
    return message_text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _load_waiting_invoice_ref_by_telegram_id(telegram_id: int) -> _TariffsInvoiceRef | None:
    async with active_session() as session:
        try:
            master = await MasterRepository(session).get_by_telegram_id(int(telegram_id))
        except MasterNotFound:
            return None
        invoice = await _load_waiting_invoice(repo=PaymentInvoiceRepository(session), master_id=int(master.id))
        if invoice is None:
            return None
        return _TariffsInvoiceRef(
            invoice_id=int(invoice.id) if getattr(invoice, "id", None) is not None else None,
            invoice_url=str(invoice.invoice_url) if getattr(invoice, "invoice_url", None) else None,
        )


async def _maybe_handle_pro_start_from_clients_limit_paywall(  # noqa: C901
    callback: CallbackQuery,
    *,
    express_pay_client: ExpressPayClient | None,
) -> bool:
    if not _is_clients_limit_paywall_message(callback):
        return False

    limit = _parse_clients_limit_from_paywall(callback)
    if limit is None:
        await callback.answer()
        return True

    settings = get_settings()
    contact = settings.billing.contact
    if express_pay_client is None:
        ev.info("billing.pro_start_unavailable", reason="no_express_pay_client", scope="paywall")
        await callback.answer()
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=txt.pro_config_missing(),
            reply_markup=_kb_config_missing(contact=contact),
            parse_mode="HTML",
        )
        if limit > 0:
            await _restore_clients_limit_paywall(callback, limit=limit)
        return True

    waiting = await _load_waiting_invoice_ref_by_telegram_id(callback.from_user.id)
    if waiting is not None and waiting.invoice_id is not None:
        await callback.answer()
        if callback.message is not None:
            await callback.message.edit_text(
                f"{paywall_txt.clients_limit_reached(limit=int(limit))}\n\n{txt.pro_waiting_invoice_notice()}",
                reply_markup=_kb_waiting_invoice(
                    invoice_url=waiting.invoice_url,
                    invoice_id=int(waiting.invoice_id),
                    contact=contact,
                ),
                parse_mode="HTML",
            )
        return True

    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            txt.pro_create_invoice_confirm(),
            reply_markup=_kb_confirm_paywall_invoice(limit=int(limit)),
            parse_mode="HTML",
        )
    return True


def _render_tariffs_base(data: _TariffsData) -> tuple[str, InlineKeyboardMarkup]:
    rows: list[list[InlineKeyboardButton]] = []
    if data.primary_text:
        rows.append(
            [
                build_upgrade_button_with_fallback(
                    contact=data.contact,
                    text=data.primary_text,
                    callback_data=_tariffs_primary_cb(source=data.source),
                    force_callback=True,
                ),
            ],
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=txt.tariffs_secondary_button(source=data.source),
                callback_data=_MASTER_SETTINGS_BACK_MENU_CB,
            ),
        ],
    )
    return data.message_text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _edit_back_to_master_tariffs(callback: CallbackQuery) -> None:
    """
    Re-render the master tariffs screen in-place (master settings flow).

    This is used to "return to previous step" after invoice creation confirmation.
    """
    state = await _load_tariffs_state(callback)
    if state is None:
        return
    message_text, reply_markup = state
    await callback.message.edit_text(message_text, reply_markup=reply_markup, parse_mode="HTML")


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

    if not result.ok:
        ev.info(
            "billing.pro_start_failed",
            error=str(result.error.value) if result.error else None,
        )
        if result.error == CreateProInvoiceError.ALREADY_PRO:
            await callback.answer(txt.pro_already_active(), show_alert=True)
            return
        await callback.answer()
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
    await callback.answer("Счёт уже создан ✅" if result.reused else "Счёт создан ✅", show_alert=False)

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


async def _create_invoice_only(
    callback: CallbackQuery,
    *,
    express_pay_client: ExpressPayClient,
    reuse_waiting: bool,
) -> CreateProInvoiceResult:
    settings = get_settings()
    price = settings.billing.pro_price_byn
    days = settings.billing.pro_days
    if price is None or days is None:
        return CreateProInvoiceResult(
            ok=False,
            error=CreateProInvoiceError.INVALID_REQUEST,
            error_detail="config_missing",
        )
    price_dec = Decimal(price)

    async with active_session() as session:
        use_case = CreateProInvoice(session, express_pay_client=express_pay_client)
        return await use_case.execute(
            CreateProInvoiceRequest(
                master_telegram_id=callback.from_user.id,
                amount=price_dec,
                currency=CurrencyCode.BYN,
                description=settings.billing.pro_description,
                lifetime_seconds=settings.billing.pro_invoice_lifetime_sec,
                reuse_waiting=reuse_waiting,
            ),
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

    if not result.ok:
        ev.info(
            "billing.pro_renew_failed",
            error=str(result.error.value) if result.error else None,
        )
        if result.error in {CreateProRenewalInvoiceError.MASTER_NOT_FOUND, CreateProRenewalInvoiceError.PROVIDER_ERROR}:
            await callback.answer()
            await callback.bot.send_message(
                chat_id=callback.from_user.id,
                text=txt.pro_error(),
                reply_markup=_kb_config_missing(contact=contact),
                parse_mode="HTML",
            )
            return
        await callback.answer()
        await _answer_with_contact(callback, contact=contact)
        return

    assert result.invoice is not None
    invoice = result.invoice
    ev.info("billing.pro_renewal_invoice_ready", invoice_id=invoice.id, reused=bool(result.reused))
    await callback.answer("Счёт уже создан ✅" if result.reused else "Счёт создан ✅", show_alert=False)

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


async def _create_renewal_invoice_only(
    callback: CallbackQuery,
    *,
    express_pay_client: ExpressPayClient,
    reuse_waiting: bool,
) -> CreateProRenewalInvoiceResult:
    settings = get_settings()
    price = settings.billing.pro_price_byn
    days = settings.billing.pro_days
    if price is None or days is None:
        return CreateProRenewalInvoiceResult(
            ok=False,
            error=CreateProRenewalInvoiceError.INVALID_REQUEST,
            error_detail="config_missing",
        )
    price_dec = Decimal(price)

    async with active_session() as session:
        use_case = CreateProRenewalInvoice(session, express_pay_client=express_pay_client)
        return await use_case.execute(
            CreateProRenewalInvoiceRequest(
                master_telegram_id=callback.from_user.id,
                amount=price_dec,
                currency=CurrencyCode.BYN,
                description=settings.billing.pro_description,
                lifetime_seconds=settings.billing.pro_invoice_lifetime_sec,
                reuse_waiting=reuse_waiting,
            ),
        )


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == _START_CB)
async def billing_pro_start(
    callback: CallbackQuery,
    express_pay_client: ExpressPayClient | None = None,
) -> None:
    bind_log_context(flow="billing_pro", step="start")
    if _is_tariffs_message(callback):
        await callback.answer()
        if callback.message is not None:
            await callback.message.edit_text(
                txt.pro_create_invoice_confirm(),
                reply_markup=_kb_confirm_invoice(action="start"),
                parse_mode="HTML",
            )
        return
    if await _maybe_handle_pro_start_from_clients_limit_paywall(callback, express_pay_client=express_pay_client):
        return
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
    if _is_tariffs_message(callback):
        await callback.answer()
        if callback.message is not None:
            await callback.message.edit_text(
                txt.pro_create_invoice_confirm(),
                reply_markup=_kb_confirm_invoice(action="renew"),
                parse_mode="HTML",
            )
        return
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


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith(_CONFIRM_CANCEL_PREFIX))
async def billing_pro_confirm_cancel(callback: CallbackQuery) -> None:
    bind_log_context(flow="billing_pro", step="confirm_cancel")
    await callback.answer()
    await _edit_back_to_master_tariffs(callback)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith(_CONFIRM_PREFIX))
async def billing_pro_confirm_yes(
    callback: CallbackQuery,
    express_pay_client: ExpressPayClient | None = None,
) -> None:
    bind_log_context(flow="billing_pro", step="confirm_yes")
    action = (callback.data or "").removeprefix(_CONFIRM_PREFIX)

    settings = get_settings()
    contact = settings.billing.contact

    if express_pay_client is None:
        ev.info("billing.pro_confirm_unavailable", reason="no_express_pay_client", action=action)
        await callback.answer()
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=txt.pro_config_missing(),
            reply_markup=_kb_config_missing(contact=contact),
            parse_mode="HTML",
        )
        await _edit_back_to_master_tariffs(callback)
        return

    if action == "start":
        result = await _create_invoice_only(callback, express_pay_client=express_pay_client, reuse_waiting=True)
        if not result.ok:
            if result.error == CreateProInvoiceError.ALREADY_PRO:
                await callback.answer(txt.pro_already_active(), show_alert=True)
            else:
                await callback.answer(txt.pro_error(), show_alert=True)
            await _edit_back_to_master_tariffs(callback)
            return
        await callback.answer("Счёт уже создан ✅" if result.reused else "Счёт создан ✅", show_alert=False)
        await _edit_back_to_master_tariffs(callback)
        return

    if action == "renew":
        result = await _create_renewal_invoice_only(callback, express_pay_client=express_pay_client, reuse_waiting=True)
        if not result.ok:
            await callback.answer(txt.pro_error(), show_alert=True)
            await _edit_back_to_master_tariffs(callback)
            return
        await callback.answer("Счёт уже создан ✅" if result.reused else "Счёт создан ✅", show_alert=False)
        await _edit_back_to_master_tariffs(callback)
        return

    await callback.answer("Неизвестное действие.", show_alert=True)
    await _edit_back_to_master_tariffs(callback)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith(_CONFIRM_PAYWALL_CANCEL_PREFIX))
async def billing_pro_paywall_confirm_cancel(callback: CallbackQuery) -> None:
    bind_log_context(flow="billing_pro", step="confirm_paywall_cancel")
    raw = (callback.data or "").removeprefix(_CONFIRM_PAYWALL_CANCEL_PREFIX)
    try:
        limit = int(raw)
    except ValueError:
        await callback.answer()
        return
    await callback.answer()
    await _restore_clients_limit_paywall(callback, limit=limit)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith(_CONFIRM_PAYWALL_PREFIX))
async def billing_pro_paywall_confirm_yes(
    callback: CallbackQuery,
    express_pay_client: ExpressPayClient | None = None,
) -> None:
    bind_log_context(flow="billing_pro", step="confirm_paywall_yes")
    raw = (callback.data or "").removeprefix(_CONFIRM_PAYWALL_PREFIX)
    try:
        limit = int(raw)
    except ValueError:
        limit = 0

    settings = get_settings()
    contact = settings.billing.contact
    if express_pay_client is None:
        ev.info("billing.pro_confirm_unavailable", reason="no_express_pay_client", scope="paywall")
        await callback.answer()
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=txt.pro_config_missing(),
            reply_markup=_kb_config_missing(contact=contact),
            parse_mode="HTML",
        )
        if limit > 0:
            await _restore_clients_limit_paywall(callback, limit=limit)
        return

    await _create_and_show_invoice(callback, express_pay_client=express_pay_client, reuse_waiting=True)
