from __future__ import annotations

from datetime import UTC, datetime
from html import escape as html_escape

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from pydantic import BaseModel, ValidationError

from src.core.sa import active_session, session_local
from src.handlers.client.client_menu import send_client_main_menu
from src.handlers.shared.flow import context_lost
from src.handlers.shared.guards import rate_limit_callback, rate_limit_message
from src.handlers.shared.ui import safe_edit_reply_markup, safe_edit_text
from src.notifications import NotificationEvent, RecipientKind
from src.notifications.context import LimitsContext
from src.notifications.notifier import NotificationRequest, Notifier
from src.notifications.policy import NotificationFacts
from src.observability.alerts import AdminAlerter
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.plans import FREE_CLIENTS_LIMIT
from src.privacy import PD_POLICY_VERSION, ConsentRole
from src.rate_limiter import RateLimiter
from src.repositories import (
    InviteNotFound,
    InviteRepository,
    MasterNotFound,
    MasterRepository,
)
from src.repositories.consent import ConsentRepository
from src.texts import client_registration as txt, personal_data as pd_txt
from src.texts.buttons import btn_back, btn_cancel, btn_confirm, btn_restart
from src.use_cases.accept_client_invite import AcceptClientInvite, AcceptClientInviteRequest, AcceptInviteError
from src.user_context import ActiveRole, UserContextStorage
from src.utils import answer_tracked, cleanup_messages, track_callback_message, track_message, validate_phone

router = Router(name=__name__)
ev = EventLogger(__name__)

CLIENT_REGISTRATION_BUCKET = "client_registration"

CLIENT_REGISTRATION_CB = {
    "confirm": "c:registration:confirm",
    "restart": "c:registration:restart",
    "cancel": "c:registration:cancel",
    "pd_agree": "c:pd:agree",
    "pd_disagree": "c:pd:disagree",
    "pd_policy": "c:pd:policy",
    "pd_back": "c:pd:back",
    "pd_understood": "c:pd:understood",
}


class ClientRegistration(StatesGroup):
    consent = State()
    consent_declined = State()
    name = State()
    phone = State()
    confirm = State()


def _build_confirm_registration_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=btn_confirm(),
                    callback_data=CLIENT_REGISTRATION_CB["confirm"],
                ),
                InlineKeyboardButton(
                    text=btn_restart(),
                    callback_data=CLIENT_REGISTRATION_CB["restart"],
                ),
            ],
            [
                InlineKeyboardButton(
                    text=btn_cancel(),
                    callback_data=CLIENT_REGISTRATION_CB["cancel"],
                ),
            ],
        ],
    )


def _build_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=btn_cancel(), callback_data=CLIENT_REGISTRATION_CB["cancel"]),
            ],
        ],
    )


def _build_pd_consent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=pd_txt.btn_agree(), callback_data=CLIENT_REGISTRATION_CB["pd_agree"]),
                InlineKeyboardButton(text=pd_txt.btn_disagree(), callback_data=CLIENT_REGISTRATION_CB["pd_disagree"]),
            ],
            [
                InlineKeyboardButton(text=pd_txt.btn_policy(), callback_data=CLIENT_REGISTRATION_CB["pd_policy"]),
            ],
            [
                InlineKeyboardButton(text=btn_cancel(), callback_data=CLIENT_REGISTRATION_CB["cancel"]),
            ],
        ],
    )


def _build_pd_declined_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=pd_txt.btn_understood(),
                    callback_data=CLIENT_REGISTRATION_CB["pd_understood"],
                ),
                InlineKeyboardButton(text=btn_back(), callback_data=CLIENT_REGISTRATION_CB["pd_back"]),
            ],
            [
                InlineKeyboardButton(text=btn_cancel(), callback_data=CLIENT_REGISTRATION_CB["cancel"]),
            ],
        ],
    )


async def _invite_preflight(*, token: str) -> tuple[bool, int | None, AcceptInviteError | None]:
    async with session_local() as session:
        invites = InviteRepository(session)
        try:
            invite = await invites.get_by_token(token)
        except InviteNotFound:
            return False, None, AcceptInviteError.INVITE_NOT_FOUND

        if not invite.is_invite_valid():
            return False, invite.master_id, AcceptInviteError.INVITE_INVALID

        if str(invite.type.value) != "CLIENT":
            return False, invite.master_id, AcceptInviteError.INVITE_WRONG_TYPE

        return True, invite.master_id, None


async def _check_if_master(telegram_id: int) -> bool:
    async with session_local() as session:
        repo = MasterRepository(session)
        try:
            await repo.get_by_telegram_id(telegram_id)
            return True
        except MasterNotFound:
            return False


async def _send_error_message(
    bot: Bot,
    chat_id: int,
    error: AcceptInviteError | None,
) -> None:
    if error is None:
        return

    if error in {AcceptInviteError.INVITE_INVALID, AcceptInviteError.INVITE_NOT_FOUND}:
        await bot.send_message(
            chat_id=chat_id,
            text=txt.err_invite_inactive(),
            parse_mode="HTML",
        )
        return

    if error in {AcceptInviteError.INVITE_WRONG_TYPE, AcceptInviteError.INVITE_MASTER_MISMATCH}:
        await bot.send_message(
            chat_id=chat_id,
            text=txt.err_invite_wrong_link(),
            parse_mode="HTML",
        )
        return

    if error == AcceptInviteError.QUOTA_EXCEEDED:
        await bot.send_message(
            chat_id=chat_id,
            text=txt.err_quota_exceeded(),
            parse_mode="HTML",
        )
        return

    if error == AcceptInviteError.PHONE_CONFLICT:
        await bot.send_message(
            chat_id=chat_id,
            text=txt.err_phone_conflict(),
            parse_mode="HTML",
        )
        return

    await bot.send_message(
        chat_id=chat_id,
        text=txt.err_generic(),
        parse_mode="HTML",
    )


class InviteData(BaseModel):
    invite_master_id: int
    invite_token: str


class _ConfirmData(BaseModel):
    invite_data: InviteData
    name: str
    phone: str


async def _reset_flow(state: FSMContext, bot: Bot, *, bucket: str) -> None:
    await cleanup_messages(state, bot, bucket=bucket)
    await state.clear()


async def _send_menu_after_registration(
    *,
    bot: Bot,
    telegram_id: int,
    user_ctx_storage: UserContextStorage,
    admin_alerter: AdminAlerter | None,
) -> None:
    try:
        is_master = await _check_if_master(telegram_id)
    except Exception as exc:
        await ev.aexception(
            "client_reg.check_master_failed",
            stage="check_master",
            exc=exc,
            admin_alerter=admin_alerter,
        )
        is_master = False
    await user_ctx_storage.set_role(telegram_id, ActiveRole.CLIENT)
    await send_client_main_menu(bot, telegram_id, show_switch_role=is_master)


async def start_client_registration(  # noqa: C901, PLR0911
    message: Message,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
    invite_link: str,
    rate_limiter: RateLimiter | None = None,
    *,
    admin_alerter: AdminAlerter | None = None,
) -> None:
    bind_log_context(flow="client_reg", step="start")
    # Reset previous attempts of this flow (single-screen UX).
    await cleanup_messages(state, message.bot, bucket=CLIENT_REGISTRATION_BUCKET)
    await state.clear()
    await track_message(state, message, bucket=CLIENT_REGISTRATION_BUCKET)

    token = invite_link.removeprefix("c_")
    if message.from_user is None:
        ev.warning("client_reg.start.no_from_user")
        return

    if not await rate_limit_message(message, rate_limiter, name="client_reg:start", ttl_sec=2):
        return

    telegram_id = message.from_user.id
    ok, invite_master_id, invite_error = await _invite_preflight(token=token)
    if not ok:
        await _send_error_message(message.bot, telegram_id, invite_error)
        await _reset_flow(state, message.bot, bucket=CLIENT_REGISTRATION_BUCKET)
        return

    await state.update_data(invite_master_id=invite_master_id, invite_token=token)

    async with session_local() as session:
        has_consent = await ConsentRepository(session).has_consent(
            telegram_id=telegram_id,
            role=str(ConsentRole.CLIENT.value),
            policy_version=str(PD_POLICY_VERSION),
        )
    if not has_consent:
        await answer_tracked(
            message,
            state,
            text=pd_txt.consent_short(),
            bucket=CLIENT_REGISTRATION_BUCKET,
            reply_markup=_build_pd_consent_keyboard(),
        )
        await state.set_state(ClientRegistration.consent)
        return

    try:
        async with active_session() as session:
            result = await AcceptClientInvite(session).execute(
                AcceptClientInviteRequest(
                    telegram_id=telegram_id,
                    invite_token=token,
                ),
            )
    except Exception as exc:
        await ev.aexception(
            "client_reg.start_failed",
            stage="use_case",
            exc=exc,
            admin_alerter=admin_alerter,
        )
        await message.answer(txt.err_generic())
        await _reset_flow(state, message.bot, bucket=CLIENT_REGISTRATION_BUCKET)
        return

    ev.info(
        "client_reg.start_result",
        ok=bool(result.ok),
        outcome=str(result.outcome.value) if result.outcome else None,
        error=str(result.error.value) if result.error else None,
        master_id=result.master_id,
    )

    if result.ok:
        await _reset_flow(state, message.bot, bucket=CLIENT_REGISTRATION_BUCKET)
        await _send_menu_after_registration(
            bot=message.bot,
            telegram_id=telegram_id,
            user_ctx_storage=user_ctx_storage,
            admin_alerter=admin_alerter,
        )
        return

    # Continue with FSM
    if result.error == AcceptInviteError.MISSING_PHONE:
        invite_data = InviteData(invite_master_id=result.master_id, invite_token=token)
        await state.update_data(invite_data=invite_data.model_dump())
        await process_name_question(message, state)
        return

    await _send_error_message(message.bot, telegram_id, result.error)
    await _reset_flow(state, message.bot, bucket=CLIENT_REGISTRATION_BUCKET)


@router.callback_query(StateFilter(ClientRegistration.consent), F.data == CLIENT_REGISTRATION_CB["pd_policy"])
async def client_reg_pd_policy(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="client_reg", step="pd_policy")
    await callback.answer()
    await track_callback_message(state, callback, bucket=CLIENT_REGISTRATION_BUCKET)
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=pd_txt.policy_in_progress(),
        parse_mode="HTML",
    )


@router.callback_query(StateFilter(ClientRegistration.consent), F.data == CLIENT_REGISTRATION_CB["pd_disagree"])
async def client_reg_pd_decline(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="client_reg", step="pd_decline")
    await callback.answer()
    await track_callback_message(state, callback, bucket=CLIENT_REGISTRATION_BUCKET)
    if callback.message is None:
        return
    await safe_edit_text(
        callback.message,
        text=pd_txt.consent_declined(),
        reply_markup=_build_pd_declined_keyboard(),
        parse_mode="HTML",
        ev=ev,
        event="client_reg.pd_declined_edit_failed",
    )
    await state.set_state(ClientRegistration.consent_declined)


@router.callback_query(StateFilter(ClientRegistration.consent_declined), F.data == CLIENT_REGISTRATION_CB["pd_back"])
async def client_reg_pd_back(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="client_reg", step="pd_back")
    await callback.answer()
    await track_callback_message(state, callback, bucket=CLIENT_REGISTRATION_BUCKET)
    if callback.message is None:
        return
    await safe_edit_text(
        callback.message,
        text=pd_txt.consent_short(),
        reply_markup=_build_pd_consent_keyboard(),
        parse_mode="HTML",
        ev=ev,
        event="client_reg.pd_back_edit_failed",
    )
    await state.set_state(ClientRegistration.consent)


@router.callback_query(
    StateFilter(ClientRegistration.consent_declined),
    F.data == CLIENT_REGISTRATION_CB["pd_understood"],
)
async def client_reg_pd_understood(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="client_reg", step="pd_understood")
    await callback.answer()
    await _reset_flow(state, callback.bot, bucket=CLIENT_REGISTRATION_BUCKET)


@router.callback_query(StateFilter(ClientRegistration.consent), F.data == CLIENT_REGISTRATION_CB["pd_agree"])
async def client_reg_pd_agree(
    callback: CallbackQuery,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
    admin_alerter: AdminAlerter | None = None,
) -> None:
    bind_log_context(flow="client_reg", step="pd_agree")
    await callback.answer()
    await track_callback_message(state, callback, bucket=CLIENT_REGISTRATION_BUCKET)
    if callback.from_user is None:
        return
    telegram_id = callback.from_user.id
    data = await state.get_data()
    token = data.get("invite_token")
    if not token:
        await context_lost(callback, state, bucket=CLIENT_REGISTRATION_BUCKET, reason="missing_invite_token_after_pd")
        return

    async with active_session() as session:
        await ConsentRepository(session).upsert_consent(
            telegram_id=telegram_id,
            role=str(ConsentRole.CLIENT.value),
            policy_version=str(PD_POLICY_VERSION),
            consented_at=datetime.now(UTC),
        )
        result = await AcceptClientInvite(session).execute(
            AcceptClientInviteRequest(
                telegram_id=telegram_id,
                invite_token=str(token),
            ),
        )

    if result.ok:
        await _reset_flow(state, callback.bot, bucket=CLIENT_REGISTRATION_BUCKET)
        await _send_menu_after_registration(
            bot=callback.bot,
            telegram_id=telegram_id,
            user_ctx_storage=user_ctx_storage,
            admin_alerter=admin_alerter,
        )
        return

    if result.error == AcceptInviteError.MISSING_PHONE:
        invite_data = InviteData(invite_master_id=result.master_id, invite_token=str(token))
        await state.update_data(invite_data=invite_data.model_dump())
        if callback.message is None:
            return
        await safe_edit_text(
            callback.message,
            text=txt.ask_name(),
            reply_markup=_build_cancel_keyboard(),
            parse_mode="HTML",
            ev=ev,
            event="client_reg.pd_ask_name_edit_failed",
        )
        await state.set_state(ClientRegistration.name)
        return

    await _send_error_message(callback.bot, telegram_id, result.error)
    await _reset_flow(state, callback.bot, bucket=CLIENT_REGISTRATION_BUCKET)


async def process_name_question(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        text=txt.ask_name(),
        bucket=CLIENT_REGISTRATION_BUCKET,
        reply_markup=_build_cancel_keyboard(),
    )
    await state.set_state(ClientRegistration.name)


@router.message(StateFilter(ClientRegistration.name))
async def process_client_name(message: Message, state: FSMContext) -> None:
    bind_log_context(flow="client_reg", step="name")
    await track_message(state, message, bucket=CLIENT_REGISTRATION_BUCKET)
    name = " ".join((message.text or "").split()).strip()
    if not name:
        ev.debug("client_reg.input_invalid", field="name", reason="empty")
        await answer_tracked(
            message,
            state,
            text=txt.name_invalid(),
            bucket=CLIENT_REGISTRATION_BUCKET,
            reply_markup=_build_cancel_keyboard(),
        )
        return

    await state.update_data(name=name)

    await answer_tracked(
        message,
        state,
        text=txt.ask_phone(name=html_escape(name)),
        bucket=CLIENT_REGISTRATION_BUCKET,
        reply_markup=_build_cancel_keyboard(),
    )
    await state.set_state(ClientRegistration.phone)


@router.message(StateFilter(ClientRegistration.phone))
async def process_client_phone(message: Message, state: FSMContext) -> None:
    bind_log_context(flow="client_reg", step="phone")
    await track_message(state, message, bucket=CLIENT_REGISTRATION_BUCKET)
    raw_text = (message.text or "").strip()
    phone = validate_phone(raw_text)
    if phone is None:
        ev.debug("client_reg.input_invalid", field="phone", reason="invalid")
        await answer_tracked(
            message,
            state,
            text=txt.phone_invalid(),
            bucket=CLIENT_REGISTRATION_BUCKET,
            reply_markup=_build_cancel_keyboard(),
        )
        return

    await state.update_data(phone=phone)

    data = await state.get_data()

    text = txt.confirm_details(
        name=html_escape(str(data["name"])),
        phone=html_escape(str(data["phone"])),
    )

    await answer_tracked(
        message,
        state,
        text=text,
        reply_markup=_build_confirm_registration_keyboard(),
        bucket=CLIENT_REGISTRATION_BUCKET,
    )
    await state.set_state(ClientRegistration.confirm)


def _load_confirm_data(state_data: dict) -> _ConfirmData | None:
    try:
        return _ConfirmData(
            invite_data=InviteData.model_validate(state_data.get("invite_data")),
            name=str(state_data.get("name") or ""),
            phone=str(state_data.get("phone") or ""),
        )
    except ValidationError:
        return None


async def _execute_confirm(
    *,
    telegram_id: int,
    data: _ConfirmData,
    admin_alerter: AdminAlerter | None,
):
    try:
        async with active_session() as session:
            return await AcceptClientInvite(session).execute(
                AcceptClientInviteRequest(
                    telegram_id=telegram_id,
                    invite_token=data.invite_data.invite_token,
                    name=data.name,
                    phone_e164=data.phone,
                    expected_master_id=data.invite_data.invite_master_id,
                ),
            )
    except Exception as exc:
        await ev.aexception(
            "client_reg.confirm_failed",
            stage="use_case",
            exc=exc,
            admin_alerter=admin_alerter,
        )
        return None


@router.callback_query(
    StateFilter(ClientRegistration.confirm),
    F.data == CLIENT_REGISTRATION_CB["confirm"],
)
async def client_reg_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
    notifier: Notifier,
    rate_limiter: RateLimiter | None = None,
    admin_alerter: AdminAlerter | None = None,
) -> None:
    bind_log_context(flow="client_reg", step="confirm")
    if not await rate_limit_callback(callback, rate_limiter, name="client_reg:confirm", ttl_sec=2):
        return
    await _client_reg_confirm_impl(
        callback=callback,
        state=state,
        user_ctx_storage=user_ctx_storage,
        notifier=notifier,
        admin_alerter=admin_alerter,
    )


async def _client_reg_confirm_impl(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
    notifier: Notifier,
    admin_alerter: AdminAlerter | None,
) -> None:
    await callback.answer()
    if callback.message is not None:
        await safe_edit_reply_markup(
            callback.message,
            reply_markup=None,
            ev=ev,
            event="client_reg.confirm.disable_keyboard_failed",
        )
    await track_callback_message(state, callback, bucket=CLIENT_REGISTRATION_BUCKET)

    state_data = await state.get_data()
    if callback.message is not None:
        await answer_tracked(
            callback.message,
            state,
            text=txt.creating_profile(),
            bucket=CLIENT_REGISTRATION_BUCKET,
        )

    telegram_id = callback.from_user.id

    confirm_data = _load_confirm_data(state_data)
    if confirm_data is None or not confirm_data.name or not confirm_data.phone:
        await context_lost(callback, state, bucket=CLIENT_REGISTRATION_BUCKET, reason="missing_confirm_data")
        return

    result = await _execute_confirm(telegram_id=telegram_id, data=confirm_data, admin_alerter=admin_alerter)
    if result is None:
        await callback.bot.send_message(chat_id=telegram_id, text=txt.err_generic(), parse_mode="HTML")
        await _reset_flow(state, callback.bot, bucket=CLIENT_REGISTRATION_BUCKET)
        return

    ev.info(
        "client_reg.confirm_result",
        ok=bool(result.ok),
        outcome=str(result.outcome.value) if result.outcome else None,
        error=str(result.error.value) if result.error else None,
        master_id=result.master_id,
        client_id=result.client_id,
    )

    bot = callback.bot
    await _reset_flow(state, callback.bot, bucket=CLIENT_REGISTRATION_BUCKET)

    if not result.ok:
        ev.warning(
            "client_reg.registration_failed",
            master_id=result.master_id,
            error=str(result.error.value) if result.error else None,
        )
        await _send_error_message(bot, telegram_id, result.error)
        return

    ev.info("client_reg.registration_success", master_id=result.master_id, client_id=result.client_id)
    await bot.send_message(
        chat_id=telegram_id,
        text=txt.done(),
        parse_mode="HTML",
    )

    if result.warn_master_clients_near_limit:
        await notifier.maybe_send(
            NotificationRequest(
                event=NotificationEvent.WARNING_NEAR_CLIENTS_LIMIT,
                recipient=RecipientKind.MASTER,
                chat_id=result.master_telegram_id,
                context=LimitsContext(
                    usage=result.usage,
                    clients_limit=FREE_CLIENTS_LIMIT,
                ),
                facts=NotificationFacts(
                    event=NotificationEvent.WARNING_NEAR_CLIENTS_LIMIT,
                    recipient=RecipientKind.MASTER,
                    chat_id=result.master_telegram_id,
                    plan_is_pro=False,
                ),
            ),
        )

    await _send_menu_after_registration(
        bot=bot,
        telegram_id=telegram_id,
        user_ctx_storage=user_ctx_storage,
        admin_alerter=admin_alerter,
    )


@router.callback_query(F.data == CLIENT_REGISTRATION_CB["confirm"])
async def client_reg_confirm_out_of_state(callback: CallbackQuery) -> None:
    bind_log_context(flow="client_reg", step="confirm_out_of_state")
    ev.debug("client_reg.confirm_out_of_state")
    await callback.answer(txt.confirm_out_of_state(), show_alert=True)


@router.callback_query(
    StateFilter(ClientRegistration.confirm),
    F.data == CLIENT_REGISTRATION_CB["restart"],
)
async def client_reg_restart(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="client_reg", step="restart")
    await callback.answer()
    await track_callback_message(state, callback, bucket=CLIENT_REGISTRATION_BUCKET)

    data = await state.get_data()
    try:
        invite_data = InviteData.model_validate(data.get("invite_data"))
    except ValidationError:
        await context_lost(callback, state, bucket=CLIENT_REGISTRATION_BUCKET, reason="restart_missing_invite_data")
        return

    await _reset_flow(state, callback.bot, bucket=CLIENT_REGISTRATION_BUCKET)
    await state.update_data(invite_data=invite_data.model_dump())

    if callback.message is None:
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=txt.ask_name(),
            reply_markup=_build_cancel_keyboard(),
            parse_mode="HTML",
        )
        await state.set_state(ClientRegistration.name)
        return
    await process_name_question(callback.message, state)


@router.callback_query(
    StateFilter(ClientRegistration.name, ClientRegistration.phone, ClientRegistration.confirm),
    F.data == CLIENT_REGISTRATION_CB["cancel"],
)
async def client_reg_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="client_reg", step="cancel")
    ev.info("client_reg.cancelled")
    await callback.answer(txt.cancel_alert(), show_alert=True)
    await _reset_flow(state, callback.bot, bucket=CLIENT_REGISTRATION_BUCKET)
