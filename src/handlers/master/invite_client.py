from enum import StrEnum
from html import escape as html_escape

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from src.core.sa import active_session
from src.handlers.shared.flow import context_lost
from src.handlers.shared.guards import rate_limit_callback
from src.handlers.shared.ui import safe_edit_text
from src.notifications.context import LimitsContext
from src.notifications.notifier import NotificationRequest, Notifier, build_facts
from src.notifications.policy import NotificationFacts
from src.notifications.renderer import render as render_notification
from src.notifications.types import NotificationEvent, RecipientKind
from src.observability.alerts import AdminAlerter
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.rate_limiter import RateLimiter
from src.texts import common as common_txt, master_invite_client as txt
from src.texts.buttons import btn_cancel
from src.use_cases.create_master_client_invite import (
    CreateMasterClientInvite,
    CreateMasterClientInviteOutcome,
)
from src.use_cases.entitlements import Usage
from src.utils import answer_tracked, cleanup_messages, track_callback_message

ev = EventLogger(__name__)
router = Router(name=__name__)

INVITE_CLIENT_BUCKET = "client_invite"


class InviteMessageType(StrEnum):
    LINK_ONLY = "link"
    FRIENDLY = "friendly"
    FORMAL = "formal"


class MasterInviteClient(StatesGroup):
    choosing_format = State()


def _build_invite_format_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=txt.btn_link_only(),
                    callback_data=f"m:invite:{InviteMessageType.LINK_ONLY}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=txt.btn_friendly(),
                    callback_data=f"m:invite:{InviteMessageType.FRIENDLY}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=txt.btn_formal(),
                    callback_data=f"m:invite:{InviteMessageType.FORMAL}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=btn_cancel(),
                    callback_data="m:invite:cancel",
                ),
            ],
        ],
    )


def _parse_invite_type(callback: CallbackQuery) -> InviteMessageType | None:
    parts = (callback.data or "").split(":")
    try:
        if len(parts) != 3:  # noqa: PLR2004
            raise ValueError()
        kind_raw = parts[2]
        return InviteMessageType(kind_raw)
    except ValueError:
        return None


async def _reset_invite_flow(state: FSMContext, bot) -> None:
    await cleanup_messages(state, bot, bucket=INVITE_CLIENT_BUCKET)
    await state.clear()


async def _maybe_send_limits_notification(
    *,
    chat_id: int,
    event: NotificationEvent,
    usage: Usage,
    clients_limit: int | None,
    plan_is_pro: bool,
    notifier: Notifier,
) -> bool:
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


def _build_near_limit_warning(
    *,
    notifier: Notifier,
    telegram_id: int,
    plan_is_pro: bool,
    usage: Usage,
    clients_limit: int | None,
) -> str:
    warn_request = NotificationRequest(
        chat_id=telegram_id,
        event=NotificationEvent.WARNING_NEAR_CLIENTS_LIMIT,
        recipient=RecipientKind.MASTER,
        context=LimitsContext(usage=usage, clients_limit=clients_limit),
        facts=NotificationFacts(
            event=NotificationEvent.WARNING_NEAR_CLIENTS_LIMIT,
            recipient=RecipientKind.MASTER,
            chat_id=telegram_id,
            plan_is_pro=plan_is_pro,
        ),
    )
    if not notifier.policy.check(build_facts(warn_request)).allowed:
        return ""

    warning_text = render_notification(
        event=NotificationEvent.WARNING_NEAR_CLIENTS_LIMIT,
        recipient=RecipientKind.MASTER,
        context=LimitsContext(usage=usage, clients_limit=clients_limit),
    ).text
    return "\n\n" + warning_text if warning_text else ""


async def start_invite_client(
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    rate_limiter: RateLimiter | None = None,
    *,
    admin_alerter: AdminAlerter | None = None,
) -> None:
    bind_log_context(flow="master_invite_client", step="start")
    if not await rate_limit_callback(callback, rate_limiter, name="master_invite_client:start", ttl_sec=2):
        return
    telegram_id = callback.from_user.id
    await cleanup_messages(state, callback.bot, bucket=INVITE_CLIENT_BUCKET)
    await state.clear()
    await track_callback_message(state, callback, bucket=INVITE_CLIENT_BUCKET)
    try:
        async with active_session() as session:
            use_case = CreateMasterClientInvite(session)
            result = await use_case.execute(master_telegram_id=telegram_id)
    except Exception as exc:
        await ev.aexception(
            "master_invite_client.start_failed",
            stage="use_case",
            exc=exc,
            admin_alerter=admin_alerter,
        )
        await callback.answer(common_txt.generic_error(), show_alert=True)
        await _reset_invite_flow(state, callback.bot)
        return

    if result.outcome == CreateMasterClientInviteOutcome.MASTER_NOT_FOUND:
        ev.warning("master_invite_client.master_not_found")
        await callback.answer(common_txt.generic_error(), show_alert=True)
        await _reset_invite_flow(state, callback.bot)
        return

    assert result.plan is not None
    assert result.usage is not None
    plan_is_pro = bool(result.plan.is_pro)

    if result.outcome == CreateMasterClientInviteOutcome.QUOTA_EXCEEDED:
        ev.info(
            "master_invite_client.quota_exceeded",
            current=result.usage.clients_count,
            limit=result.clients_limit,
        )
        sent = await _maybe_send_limits_notification(
            chat_id=telegram_id,
            event=NotificationEvent.LIMIT_CLIENTS_REACHED,
            usage=result.usage,
            clients_limit=result.clients_limit,
            plan_is_pro=plan_is_pro,
            notifier=notifier,
        )
        if not sent:
            await callback.answer(
                txt.quota_reached(current=result.usage.clients_count, limit=result.clients_limit),
                show_alert=True,
            )
        await _reset_invite_flow(state, callback.bot)
        return

    assert result.invite_link is not None
    assert result.master_name is not None
    ev.info("master_invite_client.invite_created")

    await state.update_data(
        invite_link=result.invite_link,
        master_name=result.master_name,
    )

    warning = _build_near_limit_warning(
        notifier=notifier,
        telegram_id=telegram_id,
        plan_is_pro=plan_is_pro,
        usage=result.usage,
        clients_limit=result.clients_limit,
    )

    await answer_tracked(
        callback.message,
        state,
        text=txt.invite_created(warning=warning),
        bucket=INVITE_CLIENT_BUCKET,
        reply_markup=_build_invite_format_keyboard(),
    )
    await state.set_state(MasterInviteClient.choosing_format)


@router.callback_query(
    StateFilter(MasterInviteClient.choosing_format),
    F.data.startswith("m:invite:"),
)
async def master_invite_choose_format(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_invite_client", step="choose_format")
    if callback.data == "m:invite:cancel":
        ev.info("master_invite_client.cancelled")
        await _reset_invite_flow(state, callback.bot)
        await callback.answer(
            text=txt.cancelled_hint(),
            show_alert=True,
        )
        return

    kind = _parse_invite_type(callback)
    if kind is None:
        ev.debug("master_invite_client.input_invalid", field="format")
        await callback.answer(text=txt.invalid_format(), show_alert=True)
        return

    data = await state.get_data()
    invite_link: str | None = data.get("invite_link")
    master_name: str | None = data.get("master_name")

    if not invite_link or not master_name:
        ev.warning("master_invite_client.state_invalid", reason="missing_invite_data")
        await context_lost(callback, state, bucket=INVITE_CLIENT_BUCKET, reason="missing_invite_data")
        return

    await callback.answer()
    safe_master_name = html_escape(master_name, quote=False)
    safe_invite_link = html_escape(invite_link, quote=True)
    text = txt.render_invite_message(kind=str(kind.value), master_name=safe_master_name, invite_link=safe_invite_link)

    if callback.message is not None:
        await safe_edit_text(
            callback.message,
            text=txt.done_copy_prompt(),
            ev=ev,
            event="master_invite_client.disable_keyboard_failed",
        )
        await callback.message.answer(text)
    else:
        await callback.bot.send_message(chat_id=callback.from_user.id, text=text)
    ev.info("master_invite_client.format_chosen", kind=str(kind.value))
    await _reset_invite_flow(state, callback.bot)
