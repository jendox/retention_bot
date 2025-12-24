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
from src.texts import common as common_txt, master_registration as txt
from src.texts.buttons import btn_cancel, btn_confirm, btn_restart
from src.use_cases.master_registration import (
    CompleteMasterRegistration,
    CompleteMasterRegistrationRequest,
    StartMasterRegistration,
    StartMasterRegistrationOutcome,
    StartMasterRegistrationRequest,
)
from src.user_context import ActiveRole, UserContextStorage
from src.utils import answer_tracked, cleanup_messages, track_callback_message, track_message, validate_phone

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


# ------------ helpers ------------

def _parse_work_days(raw: str) -> list[int] | None:
    """
    Parse week days:
    - "1-5" -> [0,1,2,3,4]
    - "1,3,5" -> [0,2,4]
    where 1 = monday, 7 = sunday.
    In the database saved like 0-6.
    """
    text = raw.replace(" ", "")
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


def _parse_time_range(raw: str) -> tuple[time, time] | None:
    """
    String parse "10:00-19:00" в (time(10,0), time(19,0)).
    """
    text = raw.replace(" ", "")
    if "-" not in text:
        return None
    start_str, end_str = text.split("-", 1)
    try:
        start_dt = datetime.strptime(start_str, "%H:%M")
        end_dt = datetime.strptime(end_str, "%H:%M")
    except ValueError:
        return None

    start_t = start_dt.time()
    end_t = end_dt.time()
    if start_t >= end_t:
        return None

    return start_t, end_t


def _parse_slot_size(raw: str) -> int | None:
    """
    Ожидаем количество минут. Разумные значения: 15, 20, 30, 45, 60, 90, 120.
    """
    text = raw.strip()
    if not text:
        return None
    try:
        minutes = int(text)
    except ValueError:
        return None

    allowed = {15, 20, 30, 45, 60, 90, 120}
    if minutes not in allowed:
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


async def start_master_registration(
    message: Message,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
    token: str | None = None,
) -> None:
    telegram_id = message.from_user.id
    logger.info(
        "master_reg.start",
        extra={"telegram_id": telegram_id, "has_token": bool(token)},
    )
    settings = get_settings()
    invite_secret = settings.security.master_invite_secret
    invite_only = bool(invite_secret) and not settings.security.master_public_registration

    async with session_local() as session:
        starter = StartMasterRegistration(session)
        result = await starter.execute(
            StartMasterRegistrationRequest(
                telegram_id=telegram_id,
                invite_only=invite_only,
                invite_secret=invite_secret.get_secret_value() if invite_secret is not None else None,
                token=token,
            ),
        )

    if result.outcome == StartMasterRegistrationOutcome.ALREADY_MASTER:
        await user_ctx_storage.set_role(telegram_id, ActiveRole.MASTER)
        await send_master_main_menu(message.bot, telegram_id, show_switch_role=result.is_client)
        try:
            await message.delete()
        except Exception:
            logger.debug("master_reg.delete_failed", exc_info=True)
        return

    if result.outcome == StartMasterRegistrationOutcome.INVITE_REQUIRED:
        await answer_tracked(
            message,
            state,
            text=txt.invite_required(contact=settings.billing.contact),
            bucket=MASTER_REGISTRATION_BUCKET,
        )
        return

    if result.outcome == StartMasterRegistrationOutcome.INVITE_INVALID:
        await answer_tracked(
            message,
            state,
            text=txt.invite_invalid(contact=settings.billing.contact),
            bucket=MASTER_REGISTRATION_BUCKET,
        )
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
    name = (message.text or "").strip()
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
