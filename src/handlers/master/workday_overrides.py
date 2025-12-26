from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram_calendar import SimpleCalendar, SimpleCalendarCallback

from src.core.sa import active_session, session_local
from src.datetime_utils import to_zone, utc_range_for_master_day
from src.filters.user_role import UserRole
from src.handlers.shared.guards import rate_limit_callback, rate_limit_message
from src.handlers.shared.ui import safe_bot_edit_message_text, safe_edit_text
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.rate_limiter import RateLimiter
from src.repositories import BookingRepository, MasterNotFound, MasterRepository, WorkdayOverrideRepository
from src.schemas import WorkdayOverrideCreate
from src.schemas.enums import BookingStatus
from src.texts import common as common_txt, master_overrides as txt
from src.texts.buttons import btn_back
from src.texts.master_schedule import choose_period
from src.user_context import ActiveRole
from src.utils import cleanup_messages, track_message

router = Router(name=__name__)
ev = EventLogger(__name__)

OVERRIDES_BUCKET = "master_workday_overrides"
MAIN_REF_KEY = "master_workday_overrides_main_ref"


class WorkdayOverrideStates(StatesGroup):
    picking_date = State()
    choosing_action = State()
    setting_start = State()
    setting_end = State()


def _kb_back_to_schedule() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=txt.btn_back_to_schedule(),
                    callback_data="m:overrides:back_schedule",
                ),
            ],
        ],
    )


def _kb_day_actions(*, has_override: bool) -> InlineKeyboardMarkup:
    raise RuntimeError("Use _kb_day_actions_for_master()")


def _kb_back_to_day_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=btn_back(), callback_data="m:overrides:back_day")]],
    )


def _parse_hhmm(value: str) -> time | None:
    match = re.fullmatch(r"\s*(?P<h>\d{1,2}):(?P<m>\d{2})\s*", value)
    if match is None:
        return None
    try:
        hour = int(match.group("h"))
        minute = int(match.group("m"))
        return time(hour=hour, minute=minute)
    except ValueError:
        return None


def _get_main_ref(data: dict, *, chat_id_fallback: int) -> tuple[int, int] | None:
    ref = data.get(MAIN_REF_KEY) or {}
    chat_id = int(ref.get("chat_id") or chat_id_fallback)
    message_id = ref.get("message_id")
    if message_id is None:
        return None
    return chat_id, int(message_id)


async def _set_main_ref(state: FSMContext, *, chat_id: int, message_id: int) -> None:
    await state.update_data(**{MAIN_REF_KEY: {"chat_id": int(chat_id), "message_id": int(message_id)}})


async def _edit_main(
    state: FSMContext,
    *,
    bot,
    chat_id_fallback: int,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    event: str,
) -> bool:
    data = await state.get_data()
    ref = _get_main_ref(data, chat_id_fallback=chat_id_fallback)
    if ref is None:
        return False
    chat_id, message_id = ref
    return await safe_bot_edit_message_text(
        bot,
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode="HTML",
        ev=ev,
        event=event,
    )


def _get_day_from_state(data: dict) -> date | None:
    raw = data.get("override_day")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def _get_master_id_from_state(data: dict) -> int | None:
    raw = data.get("override_master_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def _load_master_for_schedule(*, telegram_id: int):
    async with session_local() as session:
        repo = MasterRepository(session)
        return await repo.get_for_schedule_by_telegram_id(telegram_id)


async def _bookings_for_master_day(*, master_id: int, master_tz, day: date):
    async with session_local() as session:
        repo = BookingRepository(session)
        utc_range = utc_range_for_master_day(master_day=day, master_tz=master_tz)
        return await repo.get_for_master_in_range(
            master_id=master_id,
            start_at_utc=utc_range.start,
            end_at_utc=utc_range.end,
            statuses=BookingStatus.active(),
            load_clients=True,
        )


def _conflicts_for_window(*, bookings: list, master_tz, window: tuple[time, time] | None) -> list:
    if window is None:
        return list(bookings)

    start_t, end_t = window
    conflicts: list = []
    for booking in bookings:
        start_local = to_zone(booking.start_at, master_tz)
        end_local = start_local + timedelta(minutes=int(booking.duration_min))
        if start_local.time() < start_t or end_local.time() > end_t:
            conflicts.append(booking)
    return conflicts


def _format_conflicts(*, bookings: list, master_tz) -> str:
    lines = [txt.conflicts_title(), ""]
    for booking in bookings:
        start_local = to_zone(booking.start_at, master_tz)
        client = getattr(booking, "client", None)
        client_name = getattr(client, "name", None) or common_txt.label_default_client()
        lines.append(f"• {start_local:%H:%M} · {client_name}")
    lines.extend(["", txt.conflicts_hint()])
    return "\n".join(lines).strip()


def _base_work_window_for_day(master, *, day: date) -> tuple[time, time] | None:
    if day.weekday() not in set(getattr(master, "work_days", [])):
        return None
    return master.start_time, master.end_time


def _kb_day_actions_for_master(*, master, day: date) -> InlineKeyboardMarkup:
    effective_window = master.work_window_for_day(day)

    rows: list[list[InlineKeyboardButton]] = []
    if effective_window is None:
        rows.append([InlineKeyboardButton(text=txt.btn_make_working(), callback_data="m:overrides:make_working")])
    else:
        rows.append([InlineKeyboardButton(text=txt.btn_day_off(), callback_data="m:overrides:make_day_off")])

    rows.append([InlineKeyboardButton(text=txt.btn_set_hours(), callback_data="m:overrides:set_hours")])
    rows.append([InlineKeyboardButton(text=txt.btn_back_to_schedule(), callback_data="m:overrides:back_schedule")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_day_menu_main(
    state: FSMContext,
    *,
    bot,
    telegram_id: int,
    day: date,
) -> None:
    master = await _load_master_for_schedule(telegram_id=telegram_id)
    override = master.override_for_day(day)
    window = master.work_window_for_day(day)
    text = txt.day_summary(day=day, window=window, has_override=override is not None)
    reply_markup = _kb_day_actions_for_master(master=master, day=day)
    await _edit_main(
        state,
        bot=bot,
        chat_id_fallback=telegram_id,
        text=text,
        reply_markup=reply_markup,
        event="master_overrides.day_menu_failed",
    )
    await state.update_data(override_day=day.isoformat(), override_master_id=int(master.id))
    await state.set_state(WorkdayOverrideStates.choosing_action)


async def _apply_override(
    *,
    master_id: int,
    day: date,
    start_time: time | None,
    end_time: time | None,
) -> None:
    async with active_session() as session:
        master_repo = MasterRepository(session)
        override_repo = WorkdayOverrideRepository(session)
        master = await master_repo.get_for_schedule_by_id(master_id)
        existing = master.override_for_day(day)
        payload = WorkdayOverrideCreate(
            master_id=int(master.id),
            date=day,
            start_time=start_time,
            end_time=end_time,
        )
        if existing is None:
            await override_repo.create(payload)
        else:
            await override_repo.update_by_id(int(existing.id), payload)


async def _clear_override(*, master_id: int, day: date) -> None:
    async with active_session() as session:
        repo = WorkdayOverrideRepository(session)
        await repo.delete_for_master_on_date(master_id=master_id, date=day)


async def _back_to_schedule(callback: CallbackQuery, state: FSMContext) -> None:
    from src.handlers.master.schedule import _build_period_keyboard

    ok = await safe_edit_text(
        callback.message,
        text=choose_period(),
        reply_markup=_build_period_keyboard(),
        parse_mode="HTML",
        ev=ev,
        event="master_overrides.back_to_schedule_failed",
    )
    if ok:
        await cleanup_messages(state, callback.bot, bucket=OVERRIDES_BUCKET)
        await state.clear()


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == "m:overrides:start")
async def start_overrides(callback: CallbackQuery, state: FSMContext, rate_limiter: RateLimiter | None = None) -> None:
    bind_log_context(flow="master_overrides", step="start")
    if not await rate_limit_callback(callback, rate_limiter, name="master_overrides:start", ttl_sec=1):
        return
    await callback.answer()
    if callback.message is None:
        return

    await cleanup_messages(state, callback.bot, bucket=OVERRIDES_BUCKET)
    await state.clear()
    await _set_main_ref(state, chat_id=callback.message.chat.id, message_id=callback.message.message_id)

    calendar = SimpleCalendar()
    reply_markup = await calendar.start_calendar()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[*reply_markup.inline_keyboard, *_kb_back_to_schedule().inline_keyboard],
    )
    await safe_edit_text(
        callback.message,
        text=txt.choose_date(),
        reply_markup=keyboard,
        parse_mode="HTML",
        ev=ev,
        event="master_overrides.open_calendar_failed",
    )
    await state.set_state(WorkdayOverrideStates.picking_date)


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(WorkdayOverrideStates.picking_date),
    SimpleCalendarCallback.filter(),
)
async def pick_override_day(
    callback: CallbackQuery,
    callback_data: SimpleCalendarCallback,
    state: FSMContext,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="master_overrides", step="pick_day")
    if not await rate_limit_callback(callback, rate_limiter, name="master_overrides:pick_day", ttl_sec=1):
        return
    selected, picked_dt = await SimpleCalendar().process_selection(callback, callback_data)
    if not selected:
        return

    try:
        await _render_day_menu_main(state, bot=callback.bot, telegram_id=callback.from_user.id, day=picked_dt.date())
    except MasterNotFound:
        await callback.answer(common_txt.generic_error(), show_alert=True)
        await state.clear()


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(WorkdayOverrideStates.picking_date, WorkdayOverrideStates.choosing_action),
    F.data == "m:overrides:back_schedule",
)
async def back_to_schedule(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_overrides", step="back_schedule")
    await callback.answer()
    await _back_to_schedule(callback, state)


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(WorkdayOverrideStates.choosing_action),
    F.data == "m:overrides:make_day_off",
)
async def make_day_off(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_overrides", step="day_off")
    await callback.answer()
    data = await state.get_data()
    day = _get_day_from_state(data)
    master_id = _get_master_id_from_state(data)
    if day is None or master_id is None:
        await callback.answer(common_txt.context_lost(), show_alert=True)
        await state.clear()
        return

    telegram_id = callback.from_user.id
    try:
        master = await _load_master_for_schedule(telegram_id=telegram_id)
    except MasterNotFound:
        await callback.answer(common_txt.generic_error(), show_alert=True)
        await state.clear()
        return
    bookings = await _bookings_for_master_day(master_id=master_id, master_tz=master.timezone, day=day)
    conflicts = _conflicts_for_window(bookings=bookings, master_tz=master.timezone, window=None)
    if conflicts:
        await _edit_main(
            state,
            bot=callback.bot,
            chat_id_fallback=callback.from_user.id,
            text=_format_conflicts(bookings=conflicts, master_tz=master.timezone),
            reply_markup=_kb_back_to_day_actions(),
            event="master_overrides.conflicts_failed",
        )
        return

    base_window = _base_work_window_for_day(master, day=day)
    if base_window is None:
        await _clear_override(master_id=master_id, day=day)
    else:
        await _apply_override(master_id=master_id, day=day, start_time=None, end_time=None)
    await _render_day_menu_main(state, bot=callback.bot, telegram_id=telegram_id, day=day)


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(WorkdayOverrideStates.choosing_action),
    F.data == "m:overrides:make_working",
)
async def make_working(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_overrides", step="make_working")
    await callback.answer()
    data = await state.get_data()
    day = _get_day_from_state(data)
    master_id = _get_master_id_from_state(data)
    if day is None or master_id is None:
        await callback.answer(common_txt.context_lost(), show_alert=True)
        await state.clear()
        return

    try:
        master = await _load_master_for_schedule(telegram_id=callback.from_user.id)
    except MasterNotFound:
        await callback.answer(common_txt.generic_error(), show_alert=True)
        await state.clear()
        return

    base_window = _base_work_window_for_day(master, day=day)
    desired_window = base_window if base_window is not None else (master.start_time, master.end_time)

    bookings = await _bookings_for_master_day(master_id=master_id, master_tz=master.timezone, day=day)
    conflicts = _conflicts_for_window(bookings=bookings, master_tz=master.timezone, window=desired_window)
    if conflicts:
        await _edit_main(
            state,
            bot=callback.bot,
            chat_id_fallback=callback.from_user.id,
            text=_format_conflicts(bookings=conflicts, master_tz=master.timezone),
            reply_markup=_kb_back_to_day_actions(),
            event="master_overrides.conflicts_failed",
        )
        return

    if base_window is None:
        await _apply_override(
            master_id=master_id,
            day=day,
            start_time=desired_window[0],
            end_time=desired_window[1],
        )
    else:
        await _clear_override(master_id=master_id, day=day)

    await _render_day_menu_main(state, bot=callback.bot, telegram_id=callback.from_user.id, day=day)


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(WorkdayOverrideStates.choosing_action),
    F.data == "m:overrides:set_hours",
)
async def prompt_start(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_overrides", step="set_hours_start")
    await callback.answer()
    await _edit_main(
        state,
        bot=callback.bot,
        chat_id_fallback=callback.from_user.id,
        text=txt.prompt_start_time(),
        reply_markup=_kb_back_to_day_actions(),
        event="master_overrides.prompt_start_failed",
    )
    await state.set_state(WorkdayOverrideStates.setting_start)


@router.message(UserRole(ActiveRole.MASTER), StateFilter(WorkdayOverrideStates.setting_start))
async def read_start(message: Message, state: FSMContext, rate_limiter: RateLimiter | None = None) -> None:
    bind_log_context(flow="master_overrides", step="read_start")
    if not await rate_limit_message(message, rate_limiter, name="master_overrides:read_start", ttl_sec=1):
        return
    await track_message(state, message, bucket=OVERRIDES_BUCKET)

    start_time = _parse_hhmm(message.text or "")
    if start_time is None:
        await cleanup_messages(state, message.bot, bucket=OVERRIDES_BUCKET)
        await _edit_main(
            state,
            bot=message.bot,
            chat_id_fallback=message.from_user.id,
            text=f"{txt.invalid_time()}\n\n{txt.prompt_start_time()}",
            reply_markup=_kb_back_to_day_actions(),
            event="master_overrides.invalid_start_failed",
        )
        return

    await cleanup_messages(state, message.bot, bucket=OVERRIDES_BUCKET)
    await state.update_data(override_start=start_time.strftime("%H:%M"))
    await _edit_main(
        state,
        bot=message.bot,
        chat_id_fallback=message.from_user.id,
        text=txt.prompt_end_time(start_time=start_time),
        reply_markup=_kb_back_to_day_actions(),
        event="master_overrides.prompt_end_failed",
    )
    await state.set_state(WorkdayOverrideStates.setting_end)


@router.message(UserRole(ActiveRole.MASTER), StateFilter(WorkdayOverrideStates.setting_end))
async def read_end(message: Message, state: FSMContext, rate_limiter: RateLimiter | None = None) -> None:
    bind_log_context(flow="master_overrides", step="read_end")
    if not await rate_limit_message(message, rate_limiter, name="master_overrides:read_end", ttl_sec=1):
        return
    await track_message(state, message, bucket=OVERRIDES_BUCKET)

    data = await state.get_data()
    day = _get_day_from_state(data)
    master_id = _get_master_id_from_state(data)
    start_raw = data.get("override_start")
    start_time = _parse_hhmm(str(start_raw)) if start_raw else None
    if day is None or master_id is None or start_time is None:
        await message.answer(common_txt.context_lost())
        await state.clear()
        return

    end_time = _parse_hhmm(message.text or "")
    if end_time is None:
        await cleanup_messages(state, message.bot, bucket=OVERRIDES_BUCKET)
        await _edit_main(
            state,
            bot=message.bot,
            chat_id_fallback=message.from_user.id,
            text=f"{txt.invalid_time()}\n\n{txt.prompt_end_time(start_time=start_time)}",
            reply_markup=_kb_back_to_day_actions(),
            event="master_overrides.invalid_end_failed",
        )
        return

    if datetime.combine(day, end_time) <= datetime.combine(day, start_time):
        await cleanup_messages(state, message.bot, bucket=OVERRIDES_BUCKET)
        await _edit_main(
            state,
            bot=message.bot,
            chat_id_fallback=message.from_user.id,
            text=f"{txt.invalid_time_order()}\n\n{txt.prompt_end_time(start_time=start_time)}",
            reply_markup=_kb_back_to_day_actions(),
            event="master_overrides.invalid_end_order_failed",
        )
        return

    try:
        master = await _load_master_for_schedule(telegram_id=message.from_user.id)
    except MasterNotFound:
        await message.answer(common_txt.generic_error())
        await state.clear()
        return
    bookings = await _bookings_for_master_day(master_id=master_id, master_tz=master.timezone, day=day)
    conflicts = _conflicts_for_window(bookings=bookings, master_tz=master.timezone, window=(start_time, end_time))
    if conflicts:
        await cleanup_messages(state, message.bot, bucket=OVERRIDES_BUCKET)
        await _edit_main(
            state,
            bot=message.bot,
            chat_id_fallback=message.from_user.id,
            text=_format_conflicts(bookings=conflicts, master_tz=master.timezone),
            reply_markup=_kb_back_to_day_actions(),
            event="master_overrides.conflicts_failed",
        )
        return

    await cleanup_messages(state, message.bot, bucket=OVERRIDES_BUCKET)
    await _apply_override(master_id=master_id, day=day, start_time=start_time, end_time=end_time)
    await _render_day_menu_main(state, bot=message.bot, telegram_id=message.from_user.id, day=day)


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(
        WorkdayOverrideStates.choosing_action,
        WorkdayOverrideStates.setting_start,
        WorkdayOverrideStates.setting_end,
    ),
    F.data == "m:overrides:back_day",
)
async def back_to_day_actions(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_overrides", step="back_day")
    await callback.answer()
    data = await state.get_data()
    day = _get_day_from_state(data)
    if day is None:
        await callback.answer(common_txt.context_lost(), show_alert=True)
        await state.clear()
        return
    await _render_day_menu_main(state, bot=callback.bot, telegram_id=callback.from_user.id, day=day)
