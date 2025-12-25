import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from pydantic import BaseModel, ValidationError

from src.core.sa import active_session, session_local
from src.handlers.client.client_menu import send_client_main_menu
from src.notifications import NotificationEvent, RecipientKind
from src.notifications.context import LimitsContext
from src.notifications.notifier import NotificationRequest, Notifier
from src.notifications.policy import NotificationFacts
from src.observability.alerts import AdminAlerter
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.plans import FREE_CLIENTS_LIMIT
from src.repositories import (
    MasterNotFound,
    MasterRepository,
)
from src.texts import client_registration as txt
from src.texts.buttons import btn_cancel, btn_confirm, btn_restart
from src.use_cases.accept_client_invite import AcceptClientInvite, AcceptClientInviteRequest, AcceptInviteError
from src.user_context import ActiveRole, UserContextStorage
from src.utils import answer_tracked, cleanup_messages, track_callback_message, track_message, validate_phone

router = Router(name=__name__)
logger = logging.getLogger(__name__)
ev = EventLogger(__name__)

CLIENT_REGISTRATION_BUCKET = "client_registration"

CLIENT_REGISTRATION_CB = {
    "confirm": "c:registration:confirm",
    "restart": "c:registration:restart",
    "cancel": "c:registration:cancel",
}


class ClientRegistration(StatesGroup):
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
        )
        return

    if error in {AcceptInviteError.INVITE_WRONG_TYPE, AcceptInviteError.INVITE_MASTER_MISMATCH}:
        await bot.send_message(
            chat_id=chat_id,
            text=txt.err_invite_wrong_link(),
        )
        return

    if error == AcceptInviteError.QUOTA_EXCEEDED:
        await bot.send_message(
            chat_id=chat_id,
            text=txt.err_quota_exceeded(),
        )
        return

    if error == AcceptInviteError.PHONE_CONFLICT:
        await bot.send_message(
            chat_id=chat_id,
            text=txt.err_phone_conflict(),
        )
        return

    await bot.send_message(
        chat_id=chat_id,
        text=txt.err_generic(),
    )


class InviteData(BaseModel):
    invite_master_id: int
    invite_token: str


async def start_client_registration(
    message: Message,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
    invite_link: str,
    *,
    admin_alerter: AdminAlerter | None = None,
) -> None:
    bind_log_context(flow="client_reg", step="start")
    await track_message(state, message, bucket=CLIENT_REGISTRATION_BUCKET)
    token = invite_link.removeprefix("c_")
    if message.from_user is None:
        ev.warning("client_reg.start.no_from_user")
        return

    telegram_id = message.from_user.id
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
        await cleanup_messages(state, message.bot, bucket=CLIENT_REGISTRATION_BUCKET)
        await state.clear()
        return

    ev.info(
        "client_reg.start_result",
        ok=bool(result.ok),
        outcome=str(result.outcome.value) if result.outcome else None,
        error=str(result.error.value) if result.error else None,
        master_id=result.master_id,
    )

    if result.ok:
        try:
            is_master = await _check_if_master(telegram_id)
        except Exception as exc:
            await ev.aexception("client_reg.check_master_failed", stage="check_master", exc=exc, admin_alerter=admin_alerter)
            is_master = False
        await user_ctx_storage.set_role(telegram_id, ActiveRole.CLIENT)
        await send_client_main_menu(message.bot, telegram_id, show_switch_role=is_master)
        return

    # Continue with FSM
    if result.error == AcceptInviteError.MISSING_PHONE:
        invite_data = InviteData(invite_master_id=result.master_id, invite_token=token)
        await state.update_data(invite_data=invite_data.model_dump())
        await process_name_question(message, state)
        return

    await _send_error_message(message.bot, telegram_id, result.error)
    await cleanup_messages(state, message.bot, bucket=CLIENT_REGISTRATION_BUCKET)


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
        text=txt.ask_phone(name=name),
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

    text = txt.confirm_details(name=data["name"], phone=data["phone"])

    await answer_tracked(
        message,
        state,
        text=text,
        reply_markup=_build_confirm_registration_keyboard(),
        bucket=CLIENT_REGISTRATION_BUCKET,
    )
    await state.set_state(ClientRegistration.confirm)


@router.callback_query(
    StateFilter(ClientRegistration.confirm),
    F.data == CLIENT_REGISTRATION_CB["confirm"],
)
async def client_reg_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
    notifier: Notifier,
    admin_alerter: AdminAlerter | None = None,
) -> None:
    bind_log_context(flow="client_reg", step="confirm")
    await callback.answer()
    try:
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await track_callback_message(state, callback, bucket=CLIENT_REGISTRATION_BUCKET)

    data = await state.get_data()
    if data.get("confirm_in_progress"):
        ev.debug("client_reg.confirm_duplicate_click")
        return
    await state.update_data(confirm_in_progress=True)

    await answer_tracked(
        callback.message,
        state,
        text=txt.creating_profile(),
        bucket=CLIENT_REGISTRATION_BUCKET,
    )

    telegram_id = callback.from_user.id

    try:
        invite_data = InviteData.model_validate(data.get("invite_data"))
    except ValidationError:
        await callback.answer(txt.state_broken_alert(), show_alert=True)
        await cleanup_messages(state, callback.bot, bucket=CLIENT_REGISTRATION_BUCKET)
        await state.clear()
        return

    phone = data["phone"]
    name = data["name"]

    try:
        async with active_session() as session:
            result = await AcceptClientInvite(session).execute(
                AcceptClientInviteRequest(
                    telegram_id=telegram_id,
                    invite_token=invite_data.invite_token,
                    name=name,
                    phone_e164=phone,
                    expected_master_id=invite_data.invite_master_id,
                ),
            )
    except Exception as exc:
        await ev.aexception(
            "client_reg.confirm_failed",
            stage="use_case",
            exc=exc,
            admin_alerter=admin_alerter,
        )
        await callback.bot.send_message(chat_id=telegram_id, text=txt.err_generic())
        await cleanup_messages(state, callback.bot, bucket=CLIENT_REGISTRATION_BUCKET)
        await state.clear()
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
    await cleanup_messages(state, callback.bot, bucket=CLIENT_REGISTRATION_BUCKET)
    await state.clear()

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

    try:
        is_master = await _check_if_master(telegram_id)
    except Exception as exc:
        await ev.aexception("client_reg.check_master_failed", stage="check_master", exc=exc, admin_alerter=admin_alerter)
        is_master = False
    await user_ctx_storage.set_role(telegram_id, ActiveRole.CLIENT)
    await send_client_main_menu(bot, telegram_id, show_switch_role=is_master)


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
    invite_data = InviteData.model_validate(data.get("invite_data"))

    await cleanup_messages(state, callback.bot, bucket=CLIENT_REGISTRATION_BUCKET)
    await state.clear()
    await state.update_data(invite_data=invite_data.model_dump())

    await process_name_question(callback.message, state)


@router.callback_query(
    StateFilter(ClientRegistration.name, ClientRegistration.phone, ClientRegistration.confirm),
    F.data == CLIENT_REGISTRATION_CB["cancel"],
)
async def client_reg_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="client_reg", step="cancel")
    ev.info("client_reg.cancelled")
    await callback.answer(txt.cancel_alert(), show_alert=True)
    await cleanup_messages(state, callback.bot, bucket=CLIENT_REGISTRATION_BUCKET)
    await state.clear()
