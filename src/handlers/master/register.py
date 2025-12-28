"""
Master registration (Telegram bot) handler.

This file implements an aiogram FSM that collects the data required to create a master profile:
- name
- phone
- work days (0..6 in DB, where 0 is Monday)
- work time range
- slot size in minutes

Entry point is `start_master_registration(...)`, which can be called with an optional invite `token`.
Invite validation and "already a master" checks are performed in `StartMasterRegistration` use-case.
"""

from datetime import UTC, datetime, time

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import active_session, session_local
from src.handlers.master.master_menu import send_master_main_menu
from src.handlers.shared.ui import safe_delete, safe_edit_reply_markup, safe_edit_text
from src.observability.alerts import AdminAlerter
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.privacy import PD_POLICY_VERSION, ConsentRole
from src.rate_limiter import RateLimiter
from src.repositories.consent import ConsentRepository
from src.schemas.enums import Timezone
from src.settings import get_settings
from src.texts import common as common_txt, master_registration as txt, personal_data as pd_txt
from src.texts.buttons import btn_back, btn_cancel, btn_close, btn_confirm, btn_restart
from src.use_cases.master_registration import (
    CompleteMasterRegistration,
    CompleteMasterRegistrationRequest,
    StartMasterRegistration,
    StartMasterRegistrationOutcome,
    StartMasterRegistrationRequest,
    StartMasterRegistrationResult,
)
from src.user_context import ActiveRole, UserContextStorage
from src.utils import (
    answer_tracked,
    cleanup_messages,
    format_work_days_label,
    track_callback_message,
    track_message,
    validate_phone,
)

router = Router(name=__name__)
ev = EventLogger(__name__)

MASTER_REGISTRATION_BUCKET = "master_registration"


class MasterRegistration(StatesGroup):
    consent = State()
    consent_declined = State()
    name = State()
    phone = State()
    work_days = State()
    work_time = State()
    slot_size = State()
    confirm = State()


MASTER_REGISTRATION_CB = {
    "confirm": "m:registration:confirm",
    "restart": "m:registration:restart",
    "cancel": "m:registration:cancel",
    "pd_agree": "m:pd:agree",
    "pd_disagree": "m:pd:disagree",
    "pd_policy": "m:pd:policy",
    "pd_back": "m:pd:back",
    "pd_understood": "m:pd:understood",
}

_DASH_TRANSLATION = str.maketrans(dict.fromkeys("‐‑‒–—−", "-"))
_HOURS_MAX = 23
_MINUTES_MAX = 59
_NAME_MAX_LEN = 64
_SLOT_SIZE_MIN_MINUTES = 5
_SLOT_SIZE_MAX_MINUTES = 240
_SLOT_SIZE_STEP_MINUTES = 5


# ------------ helpers ------------


def _normalize_token(token: str | None) -> str | None:
    return (token or "").strip() or None


def _get_invite_policy(settings) -> tuple[bool, str | None]:
    invite_secret = settings.security.master_invite_secret
    invite_only = bool(invite_secret) and not settings.security.master_public_registration

    invite_secret_value = invite_secret.get_secret_value() if invite_secret is not None else None
    return invite_only, invite_secret_value


def _normalize_name(raw: str | None) -> str:
    return " ".join((raw or "").split()).strip()


async def _reset_master_registration(state: FSMContext, bot: Bot) -> None:
    await cleanup_messages(state, bot, bucket=MASTER_REGISTRATION_BUCKET)
    await state.clear()


def _parse_work_days(raw: str) -> list[int] | None:
    """
    Parse a week day specification entered by the user.

    - "1-5" -> [0,1,2,3,4]
    - "1,3,5" -> [0,2,4]

    User input uses 1..7 where 1 = Monday and 7 = Sunday.
    The database stores days as 0..6 where 0 = Monday.
    """
    text = raw.replace(" ", "").translate(_DASH_TRANSLATION)
    if not text:
        return None

    days: list[int] = []
    monday = 1
    sunday = 7

    try:
        if "-" in text and "," not in text:
            start_str, end_str = text.split("-", 1)
            start = int(start_str)
            end = int(end_str)
            if not (monday <= start <= sunday and monday <= end <= sunday and start <= end):
                return None
            days = [day - 1 for day in range(start, end + 1)]
        else:
            parts = text.split(",")
            for part in parts:
                day = int(part)
                if not (monday <= day <= sunday):
                    return None
                days.append(day - 1)

        days = sorted(set(days))
        return days or None
    except ValueError:
        return None


def _parse_hhmm(raw: str) -> time | None:
    raw = raw.strip()
    if ":" not in raw:
        return None
    hours_str, minutes_str = raw.split(":", 1)
    try:
        hours = int(hours_str)
        minutes = int(minutes_str)
    except ValueError:
        return None
    if not (0 <= hours <= _HOURS_MAX and 0 <= minutes <= _MINUTES_MAX):
        return None
    return time(hour=hours, minute=minutes)


def _parse_hour(raw: str) -> time | None:
    raw = raw.strip()
    try:
        hours = int(raw)
    except ValueError:
        return None
    if not (0 <= hours <= _HOURS_MAX):
        return None
    return time(hour=hours, minute=0)


def _parse_time_value(raw: str) -> time | None:
    """
    Parse either "H:MM"/"HH:MM" or "H"/"HH" (treated as full hour).
    """
    raw = raw.strip()
    if ":" in raw:
        return _parse_hhmm(raw)
    return _parse_hour(raw)


def _parse_time_range(raw: str) -> tuple[time, time] | None:
    """
    Parse a time range.

    Accepted formats:
    - "H:MM-H:MM" / "HH:MM-HH:MM"
    - "H-H" / "HH-HH" (treated as "H:00-HH:00")

    Dash can be "-", "–", "—" and similar.
    """
    text = raw.replace(" ", "").translate(_DASH_TRANSLATION)
    if "-" not in text:
        return None
    start_str, end_str = text.split("-", 1)
    start_time = _parse_time_value(start_str)
    end_time = _parse_time_value(end_str)
    if start_time is None or end_time is None:
        return None
    if start_time >= end_time:
        return None

    return start_time, end_time


def _parse_slot_size(raw: str) -> int | None:
    """
    Parse a slot size in minutes.

    Only a small set of values is allowed to keep the schedule grid predictable for clients.
    """
    text = raw.strip()
    if not text:
        return None
    try:
        minutes = int(text)
    except ValueError:
        return None

    if minutes < _SLOT_SIZE_MIN_MINUTES or minutes > _SLOT_SIZE_MAX_MINUTES:
        return None
    if minutes % _SLOT_SIZE_STEP_MINUTES != 0:
        return None
    return minutes


def _build_confirm_registration_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=btn_confirm(), callback_data=MASTER_REGISTRATION_CB["confirm"]),
                InlineKeyboardButton(text=btn_restart(), callback_data=MASTER_REGISTRATION_CB["restart"]),
            ],
            [
                InlineKeyboardButton(text=btn_cancel(), callback_data=MASTER_REGISTRATION_CB["cancel"]),
            ],
            [
                InlineKeyboardButton(text=btn_close(), callback_data="m:close"),
            ],
        ],
    )


def _build_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=btn_cancel(), callback_data=MASTER_REGISTRATION_CB["cancel"])],
            [InlineKeyboardButton(text=btn_close(), callback_data="m:close")],
        ],
    )


def _build_pd_consent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=pd_txt.btn_agree(), callback_data=MASTER_REGISTRATION_CB["pd_agree"]),
                InlineKeyboardButton(text=pd_txt.btn_disagree(), callback_data=MASTER_REGISTRATION_CB["pd_disagree"]),
            ],
            [
                InlineKeyboardButton(text=pd_txt.btn_policy(), callback_data=MASTER_REGISTRATION_CB["pd_policy"]),
            ],
        ],
    )


def _build_pd_declined_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=pd_txt.btn_understood(),
                    callback_data=MASTER_REGISTRATION_CB["pd_understood"],
                ),
                InlineKeyboardButton(text=btn_back(), callback_data=MASTER_REGISTRATION_CB["pd_back"]),
            ],
            [
                InlineKeyboardButton(text=btn_close(), callback_data="m:close"),
            ],
        ],
    )


async def _handle_start_result(
    message: Message,
    state: FSMContext,
    *,
    telegram_id: int,
    user_ctx_storage: UserContextStorage,
    contact: str,
    result: StartMasterRegistrationResult,
) -> bool:
    if result.outcome == StartMasterRegistrationOutcome.ALREADY_MASTER:
        await user_ctx_storage.set_role(telegram_id, ActiveRole.MASTER)
        await send_master_main_menu(message.bot, telegram_id, show_switch_role=result.is_client)
        await safe_delete(message, ev=ev, event="master_reg.delete_start_message_failed")
        return True

    if result.outcome == StartMasterRegistrationOutcome.INVITE_REQUIRED:
        await answer_tracked(
            message,
            state,
            text=txt.invite_required(contact=contact),
            bucket=MASTER_REGISTRATION_BUCKET,
        )
        return True

    if result.outcome == StartMasterRegistrationOutcome.INVITE_INVALID:
        await answer_tracked(
            message,
            state,
            text=txt.invite_invalid(contact=contact),
            bucket=MASTER_REGISTRATION_BUCKET,
        )
        return True

    return False


# ------------ handlers ------------


async def start_master_registration(  # noqa: C901
    message: Message,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
    rate_limiter: RateLimiter | None = None,
    admin_alerter: AdminAlerter | None = None,
    token: str | None = None,
) -> None:
    """
    Start the master registration flow.

    This handler checks whether the user is already a master and whether registration requires an invite.
    If registration can proceed, it starts the FSM by asking for the master name.
    """
    if message.from_user is None:
        ev.warning("master_reg.start.no_from_user")
        return

    bind_log_context(flow="master_reg", step="start")

    token = _normalize_token(token)
    telegram_id = message.from_user.id
    bind_log_context(has_token=bool(token))
    ev.info("master_reg.start", has_token=bool(token))

    if rate_limiter is not None:
        settings = get_settings()
        allowed = await rate_limiter.hit(
            name="master_reg:start",
            telegram_id=telegram_id,
            ttl_sec=settings.security.master_registration_start_rl_sec,
        )
        if not allowed:
            ev.debug("master_reg.rate_limited", scope="start")
            return

    await _reset_master_registration(state, message.bot)
    settings = get_settings()
    invite_only, invite_secret_value = _get_invite_policy(settings)
    bind_log_context(invite_only=bool(invite_only))
    ev.info("master_reg.invite_policy", invite_only=bool(invite_only))

    try:
        async with session_local() as session:
            result = await StartMasterRegistration(session).execute(
                StartMasterRegistrationRequest(
                    telegram_id=telegram_id,
                    invite_only=invite_only,
                    invite_secret=invite_secret_value,
                    token=token,
                ),
            )
    except Exception as exc:
        await ev.aexception(
            "master_reg.start_failed",
            stage="use_case",
            exc=exc,
            admin_alerter=admin_alerter,
        )
        await answer_tracked(
            message,
            state,
            text=common_txt.generic_error(),
            bucket=MASTER_REGISTRATION_BUCKET,
        )
        return

    ev.info(
        "master_reg.start_result",
        outcome=str(result.outcome.value),
        is_client=bool(result.is_client),
        invite_only=bool(invite_only),
        has_token=bool(token),
    )

    if await _handle_start_result(
        message,
        state,
        telegram_id=telegram_id,
        user_ctx_storage=user_ctx_storage,
        contact=settings.billing.contact,
        result=result,
    ):
        return

    await state.update_data(token=token, is_client=result.is_client)
    async with session_local() as session:
        has_consent = await ConsentRepository(session).has_consent(
            telegram_id=telegram_id,
            role=str(ConsentRole.MASTER.value),
            policy_version=str(PD_POLICY_VERSION),
        )
    if has_consent:
        await answer_tracked(
            message,
            state,
            text=txt.ask_name(),
            bucket=MASTER_REGISTRATION_BUCKET,
            reply_markup=_build_cancel_keyboard(),
        )
        await state.set_state(MasterRegistration.name)
        return

    ev.info("master_reg.pd.consent_shown", policy_version=str(PD_POLICY_VERSION))
    await answer_tracked(
        message,
        state,
        text=pd_txt.consent_short(),
        bucket=MASTER_REGISTRATION_BUCKET,
        reply_markup=_build_pd_consent_keyboard(),
    )
    await state.set_state(MasterRegistration.consent)


@router.callback_query(StateFilter(MasterRegistration.consent), F.data == MASTER_REGISTRATION_CB["pd_policy"])
async def master_reg_pd_policy(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_reg", step="pd_policy")
    await callback.answer()
    await track_callback_message(state, callback, bucket=MASTER_REGISTRATION_BUCKET)
    ev.info("master_reg.pd.policy_opened", policy_version=str(PD_POLICY_VERSION))
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=pd_txt.policy_in_progress(),
        parse_mode="HTML",
    )


@router.callback_query(StateFilter(MasterRegistration.consent), F.data == MASTER_REGISTRATION_CB["pd_disagree"])
async def master_reg_pd_decline(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_reg", step="pd_decline")
    await callback.answer()
    await track_callback_message(state, callback, bucket=MASTER_REGISTRATION_BUCKET)
    ev.info("master_reg.pd.declined", policy_version=str(PD_POLICY_VERSION))
    if callback.message is None:
        return
    await safe_edit_reply_markup(callback.message, reply_markup=None, ev=ev, event="master_reg.pd.disable_failed")
    await safe_edit_text(
        callback.message,
        text=pd_txt.consent_declined(),
        reply_markup=_build_pd_declined_keyboard(),
        parse_mode="HTML",
        ev=ev,
        event="master_reg.pd_declined_edit_failed",
    )
    await state.set_state(MasterRegistration.consent_declined)


@router.callback_query(StateFilter(MasterRegistration.consent_declined), F.data == MASTER_REGISTRATION_CB["pd_back"])
async def master_reg_pd_back(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_reg", step="pd_back")
    await callback.answer()
    await track_callback_message(state, callback, bucket=MASTER_REGISTRATION_BUCKET)
    ev.info("master_reg.pd.back_to_consent", policy_version=str(PD_POLICY_VERSION))
    if callback.message is None:
        return
    await safe_edit_text(
        callback.message,
        text=pd_txt.consent_short(),
        reply_markup=_build_pd_consent_keyboard(),
        parse_mode="HTML",
        ev=ev,
        event="master_reg.pd_back_edit_failed",
    )
    await state.set_state(MasterRegistration.consent)


@router.callback_query(
    StateFilter(MasterRegistration.consent_declined),
    F.data == MASTER_REGISTRATION_CB["pd_understood"],
)
async def master_reg_pd_understood(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_reg", step="pd_understood")
    await callback.answer()
    ev.info("master_reg.pd.decline_acknowledged", policy_version=str(PD_POLICY_VERSION))
    await _reset_master_registration(state, callback.bot)


@router.callback_query(StateFilter(MasterRegistration.consent), F.data == MASTER_REGISTRATION_CB["pd_agree"])
async def master_reg_pd_agree(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_reg", step="pd_agree")
    await callback.answer()
    await track_callback_message(state, callback, bucket=MASTER_REGISTRATION_BUCKET)
    if callback.from_user is None:
        return
    telegram_id = callback.from_user.id
    ev.info("master_reg.pd.accepted", policy_version=str(PD_POLICY_VERSION))
    async with active_session() as session:
        await ConsentRepository(session).upsert_consent(
            telegram_id=telegram_id,
            role=str(ConsentRole.MASTER.value),
            policy_version=str(PD_POLICY_VERSION),
            consented_at=datetime.now(UTC),
        )
    if callback.message is None:
        return
    await safe_edit_text(
        callback.message,
        text=txt.ask_name(),
        reply_markup=_build_cancel_keyboard(),
        parse_mode="HTML",
        ev=ev,
        event="master_reg.pd_agree_edit_failed",
    )
    await state.set_state(MasterRegistration.name)


@router.message(StateFilter(MasterRegistration.name))
async def process_master_name(message: Message, state: FSMContext) -> None:
    bind_log_context(flow="master_reg", step="name")
    await track_message(state, message, bucket=MASTER_REGISTRATION_BUCKET)
    name = _normalize_name(message.text)
    if not name:
        ev.debug("master_reg.input_invalid", field="name", reason="empty")
        await answer_tracked(
            message,
            state,
            text=txt.name_not_recognized(),
            bucket=MASTER_REGISTRATION_BUCKET,
            reply_markup=_build_cancel_keyboard(),
        )
        return

    if len(name) > _NAME_MAX_LEN:
        ev.debug("master_reg.input_invalid", field="name", reason="too_long", len=len(name), max_len=_NAME_MAX_LEN)
        await answer_tracked(
            message,
            state,
            text=txt.name_too_long(max_len=_NAME_MAX_LEN),
            bucket=MASTER_REGISTRATION_BUCKET,
            reply_markup=_build_cancel_keyboard(),
        )
        return

    await state.update_data(name=name)

    await answer_tracked(
        message,
        state,
        text=txt.ask_phone(name=name),
        bucket=MASTER_REGISTRATION_BUCKET,
        reply_markup=_build_cancel_keyboard(),
    )
    await state.set_state(MasterRegistration.phone)


@router.message(StateFilter(MasterRegistration.phone))
async def process_master_phone(message: Message, state: FSMContext) -> None:
    bind_log_context(flow="master_reg", step="phone")
    await track_message(state, message, bucket=MASTER_REGISTRATION_BUCKET)
    raw_text = (message.text or "").strip()
    phone = validate_phone(raw_text)
    if phone is None:
        ev.debug("master_reg.input_invalid", field="phone", reason="invalid")
        await answer_tracked(
            message,
            state,
            text=txt.phone_not_recognized(),
            bucket=MASTER_REGISTRATION_BUCKET,
            reply_markup=_build_cancel_keyboard(),
        )
        return

    await state.update_data(phone=phone)

    await answer_tracked(
        message,
        state,
        text=txt.ask_work_days(),
        bucket=MASTER_REGISTRATION_BUCKET,
        reply_markup=_build_cancel_keyboard(),
    )
    await state.set_state(MasterRegistration.work_days)


@router.message(StateFilter(MasterRegistration.work_days))
async def process_master_work_days(message: Message, state: FSMContext) -> None:
    bind_log_context(flow="master_reg", step="work_days")
    await track_message(state, message, bucket=MASTER_REGISTRATION_BUCKET)
    work_days = _parse_work_days(message.text or "")
    if work_days is None:
        ev.debug("master_reg.input_invalid", field="work_days", reason="invalid")
        await answer_tracked(
            message,
            state,
            text=txt.work_days_not_recognized(),
            bucket=MASTER_REGISTRATION_BUCKET,
            reply_markup=_build_cancel_keyboard(),
        )
        return

    await state.update_data(work_days=work_days)

    await answer_tracked(
        message,
        state,
        text=txt.ask_work_time(),
        bucket=MASTER_REGISTRATION_BUCKET,
        reply_markup=_build_cancel_keyboard(),
    )
    await state.set_state(MasterRegistration.work_time)


@router.message(StateFilter(MasterRegistration.work_time))
async def process_master_work_time(message: Message, state: FSMContext) -> None:
    bind_log_context(flow="master_reg", step="work_time")
    await track_message(state, message, bucket=MASTER_REGISTRATION_BUCKET)
    parsed = _parse_time_range(message.text or "")
    if parsed is None:
        ev.debug("master_reg.input_invalid", field="work_time", reason="invalid")
        await answer_tracked(
            message,
            state,
            text=txt.work_time_not_recognized(),
            bucket=MASTER_REGISTRATION_BUCKET,
            reply_markup=_build_cancel_keyboard(),
        )
        return

    start_time, end_time = parsed
    await state.update_data(
        start_time=start_time.strftime("%H:%M"),
        end_time=end_time.strftime("%H:%M"),
    )

    await answer_tracked(
        message,
        state,
        text=txt.ask_slot_size(),
        bucket=MASTER_REGISTRATION_BUCKET,
        reply_markup=_build_cancel_keyboard(),
    )
    await state.set_state(MasterRegistration.slot_size)


@router.message(StateFilter(MasterRegistration.slot_size))
async def process_master_slot_size(message: Message, state: FSMContext) -> None:
    bind_log_context(flow="master_reg", step="slot_size")
    await track_message(state, message, bucket=MASTER_REGISTRATION_BUCKET)
    slot_size = _parse_slot_size(message.text or "")
    if slot_size is None:
        ev.debug("master_reg.input_invalid", field="slot_size", reason="invalid")
        await answer_tracked(
            message,
            state,
            text=txt.slot_size_not_recognized(),
            bucket=MASTER_REGISTRATION_BUCKET,
            reply_markup=_build_cancel_keyboard(),
        )
        return

    await state.update_data(slot_size_min=slot_size)
    data = await state.get_data()

    await answer_tracked(
        message,
        state,
        text=txt.confirm(
            name=data["name"],
            phone=data["phone"],
            work_days=format_work_days_label(list(data["work_days"])),
            work_time=f"{data['start_time']}–{data['end_time']}",
            slot_size_min=int(data["slot_size_min"]),
        ),
        reply_markup=_build_confirm_registration_keyboard(),
        bucket=MASTER_REGISTRATION_BUCKET,
    )
    await state.set_state(MasterRegistration.confirm)


@router.callback_query(
    StateFilter(MasterRegistration.confirm),
    F.data == MASTER_REGISTRATION_CB["confirm"],
)
async def master_reg_confirm(  # noqa: C901
    callback: CallbackQuery,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
    rate_limiter: RateLimiter,
    admin_alerter: AdminAlerter | None = None,
) -> None:
    bind_log_context(flow="master_reg", step="confirm")
    telegram_id = callback.from_user.id
    await track_callback_message(state, callback, bucket=MASTER_REGISTRATION_BUCKET)
    settings = get_settings()
    allowed = await rate_limiter.hit(
        name="master_reg:confirm",
        telegram_id=telegram_id,
        ttl_sec=settings.security.master_registration_confirm_rl_sec,
    )
    if not allowed:
        ev.debug("master_reg.rate_limited", scope="confirm")
        await callback.answer(common_txt.too_many_requests(), show_alert=False)
        return

    await callback.answer()

    data = await state.get_data()
    if data.get("confirm_in_progress"):
        return
    await state.update_data(confirm_in_progress=True)

    if callback.message is not None:
        await safe_edit_reply_markup(
            callback.message,
            reply_markup=None,
            ev=ev,
            event="master_reg.confirm.disable_keyboard_failed",
        )

    await answer_tracked(
        callback.message,
        state,
        text=txt.creating_profile(),
        bucket=MASTER_REGISTRATION_BUCKET,
    )
    try:
        start_time = datetime.strptime(data["start_time"], "%H:%M").time()
        end_time = datetime.strptime(data["end_time"], "%H:%M").time()
    except Exception:
        ev.warning("master_reg.state_invalid", reason="time_parse_failed")
        if callback.message is not None:
            await callback.message.answer(txt.broken_state_retry(), parse_mode="HTML")
        await _reset_master_registration(state, callback.bot)
        return

    try:
        async with active_session() as session:
            result = await CompleteMasterRegistration(session).execute(
                CompleteMasterRegistrationRequest(
                    telegram_id=telegram_id,
                    name=data["name"],
                    phone=data["phone"],
                    work_days=data["work_days"],
                    start_time=start_time,
                    end_time=end_time,
                    slot_size_min=int(data["slot_size_min"]),
                    timezone=Timezone.EUROPE_MINSK,
                ),
            )
    except Exception as exc:
        await ev.aexception(
            "master_reg.complete_failed",
            stage="use_case",
            exc=exc,
            admin_alerter=admin_alerter,
        )
        if callback.message is not None:
            await callback.message.answer(
                txt.profile_creation_failed(contact=get_settings().billing.contact),
                parse_mode="HTML",
            )
        await _reset_master_registration(state, callback.bot)
        return

    ev.info(
        "master_reg.complete_result",
        master_id=result.master_id,
        outcome=str(result.outcome.value),
    )

    await callback.message.answer(txt.done(), parse_mode="HTML")
    is_client = bool(data.get("is_client"))

    await _reset_master_registration(state, callback.bot)
    await user_ctx_storage.set_role(telegram_id, ActiveRole.MASTER)
    await send_master_main_menu(callback.bot, telegram_id, show_switch_role=is_client)


@router.callback_query(
    StateFilter(MasterRegistration.confirm),
    F.data == MASTER_REGISTRATION_CB["restart"],
)
async def master_reg_restart(
    callback: CallbackQuery,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
    rate_limiter: RateLimiter,
    admin_alerter: AdminAlerter | None = None,
) -> None:
    bind_log_context(flow="master_reg", step="restart")
    data = await state.get_data()
    token = data.get("token")
    await callback.answer()
    ev.info("master_reg.restart")
    await _reset_master_registration(state, callback.bot)
    await start_master_registration(callback.message, state, user_ctx_storage, rate_limiter, admin_alerter, token)


@router.callback_query(
    StateFilter(
        MasterRegistration.name,
        MasterRegistration.phone,
        MasterRegistration.work_days,
        MasterRegistration.work_time,
        MasterRegistration.slot_size,
        MasterRegistration.confirm,
    ),
    F.data == MASTER_REGISTRATION_CB["cancel"],
)
async def master_reg_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_reg", step="cancel")
    await callback.answer(common_txt.cancelled(), show_alert=True)
    await _reset_master_registration(state, callback.bot)
