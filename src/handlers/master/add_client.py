from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import active_session
from src.filters.user_role import UserRole
from src.handlers.shared.flow import context_lost
from src.handlers.shared.guards import rate_limit_callback
from src.handlers.shared.ui import safe_edit_reply_markup, safe_edit_text
from src.notifications import NotificationEvent, RecipientKind
from src.notifications.context import LimitsContext
from src.notifications.notifier import NotificationRequest, Notifier
from src.notifications.policy import NotificationFacts
from src.observability.alerts import AdminAlerter
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.paywall import build_paywall_keyboard
from src.rate_limiter import RateLimiter
from src.settings import get_settings
from src.texts import common as common_txt, master_add_client as txt, paywall as paywall_txt
from src.texts.buttons import btn_back, btn_confirm, btn_go_pro, btn_restart
from src.use_cases.create_client_offline import (
    CreateClientOffline,
    CreateClientOfflineCreateResult,
    CreateClientOfflineError,
)
from src.use_cases.entitlements import Usage
from src.user_context import ActiveRole
from src.utils import (
    answer_tracked,
    cleanup_messages,
    track_callback_message,
    track_message,
    untrack_message_id,
    validate_phone,
)

ev = EventLogger(__name__)
router = Router(name=__name__)

ADD_CLIENT_BUCKET = "master_add_client"

CLIENT_ADD_CB = {
    "confirm": "m:add_client:confirm",
    "restart": "m:add_client:restart",
    "cancel": "m:add_client:cancel",
}


class AddClientStates(StatesGroup):
    name = State()
    phone = State()
    confirm = State()


_NAME_MAX_LEN = 64


def _build_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=btn_confirm(), callback_data=CLIENT_ADD_CB["confirm"]),
                InlineKeyboardButton(text=btn_restart(), callback_data=CLIENT_ADD_CB["restart"]),
            ],
            [
                InlineKeyboardButton(text=btn_back(), callback_data=CLIENT_ADD_CB["cancel"]),
            ],
        ],
    )


def _build_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=btn_back(), callback_data=CLIENT_ADD_CB["cancel"]),
            ],
        ],
    )


def _normalize_name(raw: str | None) -> str:
    return " ".join((raw or "").split()).strip()


async def _reset_add_client(state: FSMContext, bot) -> None:
    await cleanup_messages(state, bot, bucket=ADD_CLIENT_BUCKET)
    await state.clear()


async def _send_warning_message(
    *,
    chat_id: int,
    event: NotificationEvent,
    usage: Usage | None,
    plan_is_pro: bool | None,
    clients_limit: int | None,
    notifier: Notifier,
) -> bool:
    if clients_limit is None or usage is None:
        return False

    request = NotificationRequest(
        chat_id=chat_id,
        event=event,
        recipient=RecipientKind.MASTER,
        context=LimitsContext(usage=usage, clients_limit=clients_limit),
        facts=NotificationFacts(
            event=event,
            recipient=RecipientKind.MASTER,
            chat_id=chat_id,
            plan_is_pro=plan_is_pro,
        ),
    )
    return await notifier.maybe_send(request)


async def _handle_confirm_result(  # noqa: C901
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    telegram_id: int,
    result: CreateClientOfflineCreateResult,
) -> None:
    if result.ok:
        ev.info(
            "master_add_client.confirm_result",
            ok=True,
            master_id=result.master_id,
            client_id=result.client_id,
        )
        await callback.answer(txt.done_offline(), show_alert=True)
        await _reset_add_client(state, callback.bot)

        if result.warn_master_clients_near_limit:
            await _send_warning_message(
                chat_id=telegram_id,
                event=NotificationEvent.WARNING_NEAR_CLIENTS_LIMIT,
                usage=result.usage,
                plan_is_pro=result.plan_is_pro,
                clients_limit=result.clients_limit,
                notifier=notifier,
            )
        return

    if result.error == CreateClientOfflineError.PHONE_CONFLICT:
        ev.info(
            "master_add_client.confirm_result",
            ok=False,
            error=str(result.error.value),
            reason="phone_conflict",
        )
        await callback.answer(text=txt.err_for_preflight(result.error), show_alert=True)
        await answer_tracked(
            callback.message,
            state,
            text=txt.ask_phone_conflict_retry(),
            bucket=ADD_CLIENT_BUCKET,
            reply_markup=_build_cancel_keyboard(),
        )
        await state.set_state(AddClientStates.phone)
        await state.update_data(confirm_in_progress=False)
        return

    if result.error == CreateClientOfflineError.QUOTA_EXCEEDED:
        ev.info(
            "master_add_client.confirm_result",
            ok=False,
            error=str(result.error.value),
            clients_count=result.usage.clients_count if getattr(result, "usage", None) else None,
            clients_limit=result.clients_limit,
        )
        contact = get_settings().billing.contact
        if callback.message is not None:
            await untrack_message_id(state, bucket=ADD_CLIENT_BUCKET, message_id=callback.message.message_id)
        await cleanup_messages(state, callback.bot, bucket=ADD_CLIENT_BUCKET)
        if callback.message is not None and result.clients_limit is not None:
            await safe_edit_reply_markup(
                callback.message,
                reply_markup=None,
                ev=ev,
                event="master_add_client.disable_failed",
            )
            edited = await safe_edit_text(
                callback.message,
                text=paywall_txt.clients_limit_reached(limit=int(result.clients_limit)),
                reply_markup=build_paywall_keyboard(
                    contact=contact,
                    upgrade_text=btn_go_pro(),
                    back_text=btn_back(),
                    back_callback_data="paywall:back:clients_menu",
                    upgrade_callback_data="billing:pro:start",
                    force_upgrade_callback=True,
                ),
                parse_mode="HTML",
                ev=ev,
                event="master_add_client.paywall_edit_failed",
            )
            if not edited:
                await callback.bot.send_message(
                    chat_id=callback.from_user.id,
                    text=paywall_txt.clients_limit_reached(limit=int(result.clients_limit)),
                    reply_markup=build_paywall_keyboard(
                        contact=contact,
                        upgrade_text=btn_go_pro(),
                        back_text=btn_back(),
                        back_callback_data="paywall:back:clients_menu",
                        upgrade_callback_data="billing:pro:start",
                        force_upgrade_callback=True,
                    ),
                    parse_mode="HTML",
                )
        else:
            await callback.answer(txt.quota_reached(), show_alert=True)
        await state.clear()
        return

    ev.warning(
        "master_add_client.confirm_result",
        ok=False,
        error=str(result.error.value) if result.error else None,
    )
    await callback.answer(text=txt.err_for_preflight(result.error), show_alert=True)
    await _reset_add_client(state, callback.bot)


async def start_add_client(  # noqa: C901
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    rate_limiter: RateLimiter | None = None,
    *,
    admin_alerter: AdminAlerter | None = None,
) -> None:
    bind_log_context(flow="master_add_client", step="start")
    if not await rate_limit_callback(callback, rate_limiter, name="master_add_client:start", ttl_sec=2):
        return
    telegram_id = callback.from_user.id
    ev.info("master_add_client.start")

    try:
        async with active_session() as session:
            result = await CreateClientOffline(session).preflight(telegram_master_id=telegram_id)
    except Exception as exc:
        await ev.aexception(
            "master_add_client.preflight_failed",
            stage="use_case",
            exc=exc,
            admin_alerter=admin_alerter,
        )
        await callback.answer(common_txt.generic_error(), show_alert=True)
        await _reset_add_client(state, callback.bot)
        return

    if not result.ok:
        ev.warning(
            "master_add_client.preflight_result",
            ok=False,
            allowed=bool(result.allowed),
            error=str(result.error.value) if result.error else None,
            error_detail=result.error_detail,
        )
        await callback.answer(txt.err_for_preflight(result.error), show_alert=True)
        await _reset_add_client(state, callback.bot)
        return

    if not result.allowed:
        assert result.usage is not None
        ev.info(
            "master_add_client.quota_exceeded",
            clients_count=result.usage.clients_count,
            clients_limit=result.clients_limit,
        )
        contact = get_settings().billing.contact
        if callback.message is not None:
            await untrack_message_id(state, bucket=ADD_CLIENT_BUCKET, message_id=callback.message.message_id)
        await cleanup_messages(state, callback.bot, bucket=ADD_CLIENT_BUCKET)
        if callback.message is not None and result.clients_limit is not None:
            await callback.answer()
            edited = await safe_edit_text(
                callback.message,
                text=paywall_txt.clients_limit_reached(limit=int(result.clients_limit)),
                reply_markup=build_paywall_keyboard(
                    contact=contact,
                    upgrade_text=btn_go_pro(),
                    back_text=btn_back(),
                    back_callback_data="paywall:back:clients_menu",
                    upgrade_callback_data="billing:pro:start",
                    force_upgrade_callback=True,
                ),
                parse_mode="HTML",
                ev=ev,
                event="master_add_client.paywall_edit_failed",
            )
            if not edited:
                await callback.bot.send_message(
                    chat_id=callback.from_user.id,
                    text=paywall_txt.clients_limit_reached(limit=int(result.clients_limit)),
                    reply_markup=build_paywall_keyboard(
                        contact=contact,
                        upgrade_text=btn_go_pro(),
                        back_text=btn_back(),
                        back_callback_data="paywall:back:clients_menu",
                        upgrade_callback_data="billing:pro:start",
                        force_upgrade_callback=True,
                    ),
                    parse_mode="HTML",
                )
        else:
            await callback.answer(txt.quota_reached(), show_alert=True)
        await state.clear()
        return

    await answer_tracked(
        callback.message,
        state,
        text=txt.ask_name(show_disclaimer=bool(getattr(result, "show_offline_client_disclaimer", False))),
        bucket=ADD_CLIENT_BUCKET,
        reply_markup=_build_cancel_keyboard(),
    )
    await state.set_state(AddClientStates.name)


@router.message(UserRole(ActiveRole.MASTER), StateFilter(AddClientStates.name))
async def process_client_name(message: Message, state: FSMContext) -> None:
    bind_log_context(flow="master_add_client", step="name")
    await track_message(state, message, bucket=ADD_CLIENT_BUCKET)
    name = _normalize_name(message.text)
    if not name:
        ev.debug("master_add_client.input_invalid", field="name", reason="empty")
        await answer_tracked(
            message,
            state,
            text=txt.name_not_recognized(),
            bucket=ADD_CLIENT_BUCKET,
            reply_markup=_build_cancel_keyboard(),
        )
        return

    if len(name) > _NAME_MAX_LEN:
        ev.debug(
            "master_add_client.input_invalid",
            field="name",
            reason="too_long",
            len=len(name),
            max_len=_NAME_MAX_LEN,
        )
        await answer_tracked(
            message,
            state,
            text=txt.name_too_long(max_len=_NAME_MAX_LEN),
            bucket=ADD_CLIENT_BUCKET,
            reply_markup=_build_cancel_keyboard(),
        )
        return

    await state.update_data(name=name)
    await answer_tracked(
        message,
        state,
        text=txt.ask_phone(),
        bucket=ADD_CLIENT_BUCKET,
        reply_markup=_build_cancel_keyboard(),
    )
    await state.set_state(AddClientStates.phone)


@router.message(UserRole(ActiveRole.MASTER), StateFilter(AddClientStates.phone))
async def process_client_phone(message: Message, state: FSMContext) -> None:
    bind_log_context(flow="master_add_client", step="phone")
    await track_message(state, message, bucket=ADD_CLIENT_BUCKET)
    raw_phone = (message.text or "").strip()
    phone = validate_phone(raw_phone)
    if phone is None:
        ev.debug("master_add_client.input_invalid", field="phone", reason="invalid")
        await answer_tracked(
            message,
            state,
            text=txt.phone_not_recognized(),
            bucket=ADD_CLIENT_BUCKET,
            reply_markup=_build_cancel_keyboard(),
        )
        return

    await state.update_data(phone=phone)
    data = await state.get_data()
    await answer_tracked(
        message,
        state,
        text=txt.confirm(name=data["name"], phone=phone),
        reply_markup=_build_confirm_keyboard(),
        bucket=ADD_CLIENT_BUCKET,
    )
    await state.set_state(AddClientStates.confirm)


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(AddClientStates.confirm),
    F.data == CLIENT_ADD_CB["restart"],
)
async def master_add_client_restart(
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    admin_alerter: AdminAlerter | None = None,
) -> None:
    bind_log_context(flow="master_add_client", step="restart")
    await callback.answer()
    await track_callback_message(state, callback, bucket=ADD_CLIENT_BUCKET)
    await _reset_add_client(state, callback.bot)
    await start_add_client(callback, state, notifier, admin_alerter=admin_alerter)


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(AddClientStates.name, AddClientStates.phone, AddClientStates.confirm),
    F.data == CLIENT_ADD_CB["cancel"],
)
async def master_add_client_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_add_client", step="cancel")
    ev.info("master_add_client.cancelled")
    await callback.answer()
    await _reset_add_client(state, callback.bot)


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(AddClientStates.confirm),
    F.data == CLIENT_ADD_CB["confirm"],
)
async def master_add_client_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    rate_limiter: RateLimiter | None = None,
    admin_alerter: AdminAlerter | None = None,
) -> None:
    bind_log_context(flow="master_add_client", step="confirm")
    if not await rate_limit_callback(callback, rate_limiter, name="master_add_client:confirm", ttl_sec=2):
        return
    telegram_id = callback.from_user.id
    await track_callback_message(state, callback, bucket=ADD_CLIENT_BUCKET)
    await callback.answer()

    data = await state.get_data()
    if data.get("confirm_in_progress"):
        ev.debug("master_add_client.confirm_duplicate_click")
        return
    await state.update_data(confirm_in_progress=True)

    if callback.message is not None:
        await safe_edit_reply_markup(
            callback.message,
            reply_markup=None,
            ev=ev,
            event="master_add_client.confirm.disable_keyboard_failed",
        )

    name = data.get("name")
    phone = data.get("phone")
    if not name or not phone:
        ev.warning(
            "master_add_client.state_invalid",
            reason="missing_data",
            has_name=bool(name),
            has_phone=bool(phone),
        )
        await context_lost(callback, state, bucket=ADD_CLIENT_BUCKET, reason="missing_data")
        return

    try:
        async with active_session() as session:
            result = await CreateClientOffline(session).create(
                telegram_master_id=telegram_id,
                phone_e164=phone,
                name=name,
            )
    except Exception as exc:
        await ev.aexception(
            "master_add_client.confirm_failed",
            stage="use_case",
            exc=exc,
            admin_alerter=admin_alerter,
        )
        await callback.bot.send_message(chat_id=telegram_id, text=common_txt.generic_error())
        await _reset_add_client(state, callback.bot)
        return

    await _handle_confirm_result(
        callback=callback,
        state=state,
        notifier=notifier,
        telegram_id=telegram_id,
        result=result,
    )
