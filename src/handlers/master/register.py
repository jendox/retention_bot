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

import logging
from datetime import datetime, time

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import active_session, session_local
from src.handlers.master.master_menu import send_master_main_menu
from src.schemas.enums import Timezone
from src.settings import get_settings
from src.texts import admin as admin_txt, common as common_txt, master_registration as txt
from src.texts.buttons import btn_cancel, btn_confirm, btn_restart
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
    notify_admins,
    track_callback_message,
    track_message,
    validate_phone,
)

router = Router(name=__name__)
logger = logging.getLogger(__name__)

MASTER_REGISTRATION_BUCKET = "master_registration"


class MasterRegistration(StatesGroup):
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
}

_DASH_TRANSLATION = str.maketrans(dict.fromkeys("‐‑‒–—−", "-"))
_HOURS_MAX = 23
_MINUTES_MAX = 59
_INVITE_MISCONFIGURED_NOTIFIED = False
_NAME_MAX_LEN = 64
_SLOT_SIZE_MIN_MINUTES = 5
_SLOT_SIZE_MAX_MINUTES = 240
_SLOT_SIZE_STEP_MINUTES = 5


# ------------ helpers ------------

def _normalize_token(token: str | None) -> str | None:
    return (token or "").strip() or None


def _get_invite_policy(settings, *, telegram_id: int) -> tuple[bool, str | None]:
    invite_secret = settings.security.master_invite_secret
    invite_only = bool(invite_secret) and not settings.security.master_public_registration

    if not settings.security.master_public_registration and invite_secret is None:
        logger.warning(
            "master_reg.invite_misconfigured",
            extra={"telegram_id": telegram_id},
        )

    invite_secret_value = invite_secret.get_secret_value() if invite_secret is not None else None
    return invite_only, invite_secret_value


def _normalize_name(raw: str | None) -> str:
    return " ".join((raw or "").split()).strip()


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
        ],
    )


def _build_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=btn_cancel(), callback_data=MASTER_REGISTRATION_CB["cancel"])],
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
        try:
            await message.delete()
        except Exception:
            logger.debug("master_reg.delete_failed", exc_info=True)
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

async def start_master_registration(
    message: Message,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
    token: str | None = None,
) -> None:
    """
    Start the master registration flow.

    This handler checks whether the user is already a master and whether registration requires an invite.
    If registration can proceed, it starts the FSM by asking for the master name.
    """
    if message.from_user is None:
        logger.warning("master_reg.start.no_from_user")
        return

    token = _normalize_token(token)

    await cleanup_messages(state, message.bot, bucket=MASTER_REGISTRATION_BUCKET)
    await state.clear()

    telegram_id = message.from_user.id
    logger.info(
        "master_reg.start",
        extra={"telegram_id": telegram_id, "has_token": bool(token)},
    )
    settings = get_settings()
    invite_only, invite_secret_value = _get_invite_policy(settings, telegram_id=telegram_id)
    if not settings.security.master_public_registration and settings.security.master_invite_secret is None:
        global _INVITE_MISCONFIGURED_NOTIFIED  # noqa: PLW0603
        if not _INVITE_MISCONFIGURED_NOTIFIED:
            _INVITE_MISCONFIGURED_NOTIFIED = True
            await notify_admins(
                message.bot,
                settings.admin.telegram_ids,
                admin_txt.invite_policy_misconfigured(),
            )

    async with session_local() as session:
        result = await StartMasterRegistration(session).execute(
            StartMasterRegistrationRequest(
                telegram_id=telegram_id,
                invite_only=invite_only,
                invite_secret=invite_secret_value,
                token=token,
            ),
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
    await answer_tracked(
        message,
        state,
        text=txt.ask_name(),
        bucket=MASTER_REGISTRATION_BUCKET,
        reply_markup=_build_cancel_keyboard(),
    )
    await state.set_state(MasterRegistration.name)


@router.message(StateFilter(MasterRegistration.name))
async def process_master_name(message: Message, state: FSMContext) -> None:
    await track_message(state, message, bucket=MASTER_REGISTRATION_BUCKET)
    name = _normalize_name(message.text)
    if not name:
        logger.debug(
            "master_reg.name.invalid",
            extra={"telegram_id": message.from_user.id if message.from_user else None},
        )
        await answer_tracked(
            message,
            state,
            text=txt.name_not_recognized(),
            bucket=MASTER_REGISTRATION_BUCKET,
            reply_markup=_build_cancel_keyboard(),
        )
        return

    if len(name) > _NAME_MAX_LEN:
        logger.debug(
            "master_reg.name.too_long",
            extra={"telegram_id": message.from_user.id if message.from_user else None, "len": len(name)},
        )
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
    await track_message(state, message, bucket=MASTER_REGISTRATION_BUCKET)
    raw_text = (message.text or "").strip()
    phone = validate_phone(raw_text)
    if phone is None:
        logger.debug(
            "master_reg.phone.invalid",
            extra={
                "telegram_id": message.from_user.id if message.from_user else None,
                "raw": raw_text,
            },
        )
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
    await track_message(state, message, bucket=MASTER_REGISTRATION_BUCKET)
    work_days = _parse_work_days(message.text or "")
    if work_days is None:
        logger.debug(
            "master_reg.work_days.invalid",
            extra={
                "telegram_id": message.from_user.id if message.from_user else None,
                "raw": message.text,
            },
        )
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
    await track_message(state, message, bucket=MASTER_REGISTRATION_BUCKET)
    parsed = _parse_time_range(message.text or "")
    if parsed is None:
        logger.debug(
            "master_reg.work_time.invalid",
            extra={
                "telegram_id": message.from_user.id if message.from_user else None,
                "raw": message.text,
            },
        )
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
    await track_message(state, message, bucket=MASTER_REGISTRATION_BUCKET)
    slot_size = _parse_slot_size(message.text or "")
    if slot_size is None:
        logger.debug(
            "master_reg.slot_size.invalid",
            extra={
                "telegram_id": message.from_user.id if message.from_user else None,
                "raw": message.text,
            },
        )
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
            work_days=", ".join(str(day + 1) for day in data["work_days"]),
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
async def master_reg_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
) -> None:
    telegram_id = callback.from_user.id
    await track_callback_message(state, callback, bucket=MASTER_REGISTRATION_BUCKET)
    logger.info("master_reg.confirm", extra={"telegram_id": telegram_id})
    await answer_tracked(
        callback.message,
        state,
        text=txt.creating_profile(),
        bucket=MASTER_REGISTRATION_BUCKET,
    )
    data = await state.get_data()
    try:
        start_time = datetime.strptime(data["start_time"], "%H:%M").time()
        end_time = datetime.strptime(data["end_time"], "%H:%M").time()
    except Exception:
        await callback.answer(txt.broken_state_retry(), show_alert=True)
        await cleanup_messages(state, callback.bot, bucket=MASTER_REGISTRATION_BUCKET)
        await state.clear()
        return

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
        logger.info("master.registration.completed", extra={"master_id": result.master_id, "telegram_id": telegram_id})

    await callback.message.answer(txt.done())
    is_client = bool(data.get("is_client"))

    await cleanup_messages(state, callback.bot, bucket=MASTER_REGISTRATION_BUCKET)
    await state.clear()
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
) -> None:
    data = await state.get_data()
    token = data.get("token")
    await callback.answer()
    logger.info(
        "master_reg.restart",
        extra={"telegram_id": callback.from_user.id if callback.from_user else None},
    )
    await cleanup_messages(state, callback.bot, bucket=MASTER_REGISTRATION_BUCKET)
    await state.clear()
    await start_master_registration(callback.message, state, user_ctx_storage, token)


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
    await callback.answer(common_txt.cancelled(), show_alert=True)
    await cleanup_messages(state, callback.bot, bucket=MASTER_REGISTRATION_BUCKET)
    await state.clear()
