from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape as html_escape

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram_calendar import SimpleCalendar, SimpleCalendarCallback

from src.core.sa import active_session, session_local
from src.datetime_utils import get_timezone, to_zone
from src.handlers.shared.flow import context_lost
from src.handlers.shared.guards import rate_limit_callback
from src.handlers.shared.ui import safe_edit_reply_markup, safe_edit_text
from src.notifications import BookingContext, NotificationEvent, RecipientKind
from src.notifications.context import LimitsContext
from src.notifications.notifier import NotificationRequest, Notifier
from src.notifications.policy import NotificationFacts
from src.observability.alerts import AdminAlerter
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.rate_limiter import RateLimiter
from src.repositories import MasterRepository
from src.schemas import MasterWithClients
from src.schemas.enums import Timezone
from src.texts import common as common_txt, master_add_booking as txt
from src.texts.buttons import btn_cancel, btn_cancel_booking, btn_confirm
from src.use_cases.create_master_booking import (
    CreateMasterBooking,
    CreateMasterBookingError,
    CreateMasterBookingRequest,
    CreateMasterBookingResult,
)
from src.use_cases.entitlements import EntitlementsService
from src.use_cases.master_free_slots import GetMasterFreeSlots
from src.utils import answer_tracked, cleanup_messages, track_callback_message, track_message

ev = EventLogger(__name__)
router = Router(name=__name__)

ADD_BOOKING_BUCKET = "master_add_booking"


class AddBookingStates(StatesGroup):
    search_client = State()
    selecting_date = State()
    selecting_slot = State()
    confirm = State()


@dataclass(frozen=True)
class _ConfirmInput:
    slot_dt: datetime
    client: dict
    master_id: int
    master_tz_enum: Timezone
    master_day: date | None
    slots: list[datetime]


def _filter_clients(clients: Iterable, query: str) -> list:
    q = query.lower()
    result = []
    for client in clients:
        name = getattr(client, "name", "") or ""
        phone = getattr(client, "phone", "") or ""
        if q in name.lower() or q in phone:
            result.append(client)
    return result


def _build_clients_keyboard(clients: list) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for client in clients[:10]:
        label = client.name
        if client.phone:
            label += f" ({client.phone})"
        if client.telegram_id is None:
            label += txt.label_offline()
        rows.append([InlineKeyboardButton(text=label, callback_data=f"m:add_booking:client:{client.id}")])
    rows.append([InlineKeyboardButton(text=btn_cancel(), callback_data="m:add_booking:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_slots_keyboard(slots: list[datetime]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, slot in enumerate(slots):
        rows.append([InlineKeyboardButton(text=slot.strftime("%H:%M"), callback_data=f"m:add_booking:slot:{index}")])
    rows.append([InlineKeyboardButton(text=btn_cancel(), callback_data="m:add_booking:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=btn_confirm(), callback_data="m:add_booking:confirm"),
                InlineKeyboardButton(text=btn_cancel(), callback_data="m:add_booking:cancel"),
            ],
        ],
    )


ASYNC_CTX_ERROR = common_txt.generic_error()


async def _load_master_with_clients(telegram_id: int) -> MasterWithClients:
    async with session_local() as session:
        repo = MasterRepository(session)
        return await repo.get_with_clients_by_telegram_id(telegram_id)


async def _reset_add_booking(state: FSMContext, bot) -> None:
    await cleanup_messages(state, bot, bucket=ADD_BOOKING_BUCKET)
    await state.clear()


async def _send_and_track(
    *,
    state: FSMContext,
    bot,
    chat_id: int,
    text: str,
    reply_markup=None,
) -> None:
    msg = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    await track_message(state, msg, bucket=ADD_BOOKING_BUCKET)


async def _restore_calendar(callback: CallbackQuery, state: FSMContext) -> None:
    calendar = SimpleCalendar()
    reply_markup = await calendar.start_calendar()
    if callback.message is not None:
        ok = await safe_edit_text(
            callback.message,
            text=txt.choose_date(),
            reply_markup=reply_markup,
            ev=ev,
            event="master_add_booking.edit_failed",
        )
        if ok:
            return
    await _send_and_track(
        state=state,
        bot=callback.bot,
        chat_id=callback.from_user.id,
        text=txt.choose_date(),
        reply_markup=reply_markup,
    )


async def _validate_picked_day_in_horizon(
    session,
    *,
    master_id: int,
    picked_date: datetime,
    master_tz_enum: Timezone,
) -> tuple[bool, date, date, date]:
    entitlements = EntitlementsService(session)
    horizon_days = await entitlements.max_booking_horizon_days(master_id=master_id)
    master_tz_info = get_timezone(str(master_tz_enum.value))
    today_master = datetime.now(tz=master_tz_info).date()
    picked_day = picked_date.date() if picked_date.tzinfo is None else picked_date.astimezone(master_tz_info).date()
    max_date = today_master + timedelta(days=horizon_days)
    return (today_master <= picked_day <= max_date), picked_day, today_master, max_date


async def _pick_date_state(state: FSMContext) -> tuple[int, Timezone] | None:
    data = await state.get_data()
    master_id = data.get("master_id")
    master_timezone = data.get("master_timezone")
    if master_id is None or master_timezone is None:
        return None
    return int(master_id), Timezone(master_timezone)


async def _edit_or_send(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    if callback.message is not None:
        ok = await safe_edit_text(
            callback.message,
            text=text,
            reply_markup=reply_markup,
            ev=ev,
            event="master_add_booking.edit_failed",
        )
        if ok:
            return
    await _send_and_track(
        state=state,
        bot=callback.bot,
        chat_id=callback.from_user.id,
        text=text,
        reply_markup=reply_markup,
    )


def _parse_confirm_input(data: dict) -> _ConfirmInput | None:
    slot_iso = data.get("selected_slot")
    client = data.get("client", {})
    master_id = data.get("master_id")
    master_timezone = data.get("master_timezone")
    if slot_iso is None or master_id is None or not client or master_timezone is None:
        return None
    try:
        slot_dt = datetime.fromisoformat(str(slot_iso))
        master_tz_enum = Timezone(str(master_timezone))
    except ValueError:
        return None

    master_day = _parse_iso_date(data.get("master_day"))
    slots = [datetime.fromisoformat(x) for x in (data.get("slots") or [])]
    return _ConfirmInput(
        slot_dt=slot_dt,
        client=dict(client),
        master_id=int(master_id),
        master_tz_enum=master_tz_enum,
        master_day=master_day,
        slots=slots,
    )


async def _create_booking(
    request: CreateMasterBookingRequest,
    *,
    admin_alerter: AdminAlerter | None,
) -> CreateMasterBookingResult:
    try:
        async with active_session() as session:
            return await CreateMasterBooking(session).execute(request)
    except Exception as exc:
        await ev.aexception("master_add_booking.confirm_failed", stage="use_case", exc=exc, admin_alerter=admin_alerter)
        raise


async def _handle_slot_taken(callback: CallbackQuery, state: FSMContext, *, confirm: _ConfirmInput) -> None:
    await callback.answer(text=txt.slot_taken(), show_alert=True)
    await state.update_data(confirm_in_progress=False)
    if confirm.master_day and confirm.slots:
        slots_markup = _build_slots_keyboard([to_zone(dt, confirm.master_tz_enum) for dt in confirm.slots])
        await _edit_or_send(
            callback,
            state,
            text=txt.slots_title(day=confirm.master_day),
            reply_markup=slots_markup,
        )
        await state.set_state(AddBookingStates.selecting_slot)
        return
    await _restore_calendar(callback, state)
    await state.set_state(AddBookingStates.selecting_date)


async def _handle_quota_exceeded(
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    *,
    result: CreateMasterBookingResult,
) -> None:
    warned = False
    if result.usage is not None and result.bookings_limit is not None:
        warned = await notifier.maybe_send(
            NotificationRequest(
                event=NotificationEvent.LIMIT_BOOKINGS_REACHED,
                recipient=RecipientKind.MASTER,
                chat_id=callback.from_user.id,
                context=LimitsContext(
                    usage=result.usage,
                    bookings_limit=int(result.bookings_limit),
                ),
                facts=NotificationFacts(
                    event=NotificationEvent.LIMIT_BOOKINGS_REACHED,
                    recipient=RecipientKind.MASTER,
                    chat_id=callback.from_user.id,
                    plan_is_pro=bool(result.plan_is_pro),
                ),
            ),
        )
    if not warned:
        await callback.answer(text=txt.quota_reached(), show_alert=True)
    await _reset_add_booking(state, callback.bot)


async def _handle_success(
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    *,
    confirm: _ConfirmInput,
    result: CreateMasterBookingResult,
) -> None:
    booking = result.booking
    master = result.master
    if booking is None or master is None:
        ev.warning("master_add_booking.state_invalid", reason="missing_result_objects")
        await context_lost(callback, state, bucket=ADD_BOOKING_BUCKET, reason="missing_result_objects")
        return

    if result.warn_master_bookings_near_limit and result.usage is not None and result.bookings_limit is not None:
        await notifier.maybe_send(
            NotificationRequest(
                event=NotificationEvent.WARNING_NEAR_BOOKINGS_LIMIT,
                recipient=RecipientKind.MASTER,
                chat_id=callback.from_user.id,
                context=LimitsContext(
                    usage=result.usage,
                    bookings_limit=int(result.bookings_limit),
                ),
                facts=NotificationFacts(
                    event=NotificationEvent.WARNING_NEAR_BOOKINGS_LIMIT,
                    recipient=RecipientKind.MASTER,
                    chat_id=callback.from_user.id,
                    plan_is_pro=bool(result.plan_is_pro),
                ),
            ),
        )

    client_has_tg = confirm.client.get("telegram_id") is not None
    await callback.answer(text=txt.created(client_has_tg=client_has_tg), show_alert=True)

    if client_has_tg:
        allow_client_notifications = (
            bool(result.plan_is_pro)
            and bool(getattr(master, "notify_clients", True))
            and bool(confirm.client.get("notifications_enabled", True))
        )
        if allow_client_notifications:
            client_tz_val = confirm.client.get("timezone")
            client_tz_enum = Timezone(client_tz_val) if client_tz_val else None
            slot_client = to_zone(confirm.slot_dt, client_tz_enum) if client_tz_enum else confirm.slot_dt

            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=btn_cancel_booking(), callback_data=f"c:booking:{booking.id}:cancel")],
                ],
            )

            await notifier.maybe_send(
                NotificationRequest(
                    event=NotificationEvent.BOOKING_CREATED_CONFIRMED,
                    recipient=RecipientKind.CLIENT,
                    chat_id=confirm.client["telegram_id"],
                    context=BookingContext(
                        booking_id=booking.id,
                        master_name=master.name,
                        client_name=confirm.client.get("name") or "",
                        slot_str=slot_client.strftime("%d.%m.%Y %H:%M"),
                        duration_min=master.slot_size_min,
                    ),
                    facts=NotificationFacts(
                        event=NotificationEvent.BOOKING_CREATED_CONFIRMED,
                        recipient=RecipientKind.CLIENT,
                        chat_id=confirm.client["telegram_id"],
                        plan_is_pro=bool(result.plan_is_pro),
                        master_notify_clients=allow_client_notifications,
                        client_notifications_enabled=bool(confirm.client.get("notifications_enabled", True)),
                        booking_start_at_utc=confirm.slot_dt,
                    ),
                    reply_markup=reply_markup,
                ),
            )

    await cleanup_messages(state, callback.bot, bucket=ADD_BOOKING_BUCKET)
    await state.clear()


async def _handle_confirm_result(
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    *,
    confirm: _ConfirmInput,
    result: CreateMasterBookingResult,
) -> None:
    if not result.ok:
        if result.error == CreateMasterBookingError.QUOTA_EXCEEDED:
            await _handle_quota_exceeded(callback, state, notifier, result=result)
            return
        if result.error == CreateMasterBookingError.SLOT_NOT_AVAILABLE:
            await _handle_slot_taken(callback, state, confirm=confirm)
            return
        await callback.answer(ASYNC_CTX_ERROR, show_alert=True)
        await _reset_add_booking(state, callback.bot)
        return

    await _handle_success(callback, state, notifier, confirm=confirm, result=result)


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


async def start_add_booking(message: Message, state: FSMContext) -> None:
    bind_log_context(flow="master_add_booking", step="start")
    if message.from_user is None:
        ev.warning("master_add_booking.start.no_from_user")
        return
    ev.info("master_add_booking.start")
    await _reset_add_booking(state, message.bot)
    await track_message(state, message, bucket=ADD_BOOKING_BUCKET)
    await answer_tracked(
        message,
        state,
        text=txt.ask_query(),
        bucket=ADD_BOOKING_BUCKET,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=btn_cancel(), callback_data="m:add_booking:cancel")]],
        ),
    )
    await state.set_state(AddBookingStates.search_client)


@router.message(StateFilter(AddBookingStates.search_client))
async def search_client(message: Message, state: FSMContext) -> None:
    bind_log_context(flow="master_add_booking", step="search_client")
    await track_message(state, message, bucket=ADD_BOOKING_BUCKET)
    query = (message.text or "").strip()
    if not query:
        ev.debug("master_add_booking.input_invalid", field="query", reason="empty")
        await answer_tracked(
            message,
            state,
            text=txt.query_required(),
            bucket=ADD_BOOKING_BUCKET,
        )
        return

    if message.from_user is None:
        ev.warning("master_add_booking.search_client.no_from_user")
        return

    telegram_id = message.from_user.id
    try:
        master = await _load_master_with_clients(telegram_id)
    except Exception as exc:
        await ev.aexception("master_add_booking.search_client_failed", stage="load_master", exc=exc)
        await message.answer(ASYNC_CTX_ERROR)
        await _reset_add_booking(state, message.bot)
        return

    matches = _filter_clients(master.clients, query)
    if not matches:
        ev.info("master_add_booking.search_result", outcome="no_matches", query_len=len(query))
        await answer_tracked(
            message,
            state,
            text=txt.no_matches(),
            bucket=ADD_BOOKING_BUCKET,
        )
        return

    ev.info("master_add_booking.search_result", outcome="matches", matches=len(matches), query_len=len(query))
    await state.update_data(
        master_id=master.id,
        master_slot_size=master.slot_size_min,
        master_timezone=str(master.timezone.value),
        master_day=None,
        clients=[client.to_state_dict() for client in matches],
    )
    await answer_tracked(
        message,
        state,
        text=txt.choose_client(),
        reply_markup=_build_clients_keyboard(matches),
        bucket=ADD_BOOKING_BUCKET,
    )


def _get_selected_client(data: dict, client_id: int):
    for raw in data.get("clients", []):
        if raw.get("id") == client_id:
            return raw
    return None


@router.callback_query(StateFilter(AddBookingStates.search_client), F.data.startswith("m:add_booking:client:"))
async def choose_client(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_add_booking", step="choose_client")
    await track_callback_message(state, callback, bucket=ADD_BOOKING_BUCKET)
    _, _, _, client_id_str = callback.data.split(":", 3)
    try:
        client_id = int(client_id_str)
    except ValueError:
        ev.warning("master_add_booking.input_invalid", field="client_id", reason="parse_error")
        await callback.answer(ASYNC_CTX_ERROR, show_alert=True)
        return

    data = await state.get_data()
    client = _get_selected_client(data, client_id)
    if client is None:
        ev.warning("master_add_booking.state_invalid", reason="client_not_in_state", client_id=client_id)
        await callback.answer(ASYNC_CTX_ERROR, show_alert=True)
        return

    ev.info("master_add_booking.client_selected", client_id=client_id)
    await state.update_data(client=client)

    calendar = SimpleCalendar()
    reply_markup = await calendar.start_calendar()

    if callback.message is not None:
        ok = await safe_edit_text(
            callback.message,
            text=txt.choose_date(),
            reply_markup=reply_markup,
            ev=ev,
            event="master_add_booking.edit_failed",
        )
        if not ok:
            await _send_and_track(
                state=state,
                bot=callback.bot,
                chat_id=callback.from_user.id,
                text=txt.choose_date(),
                reply_markup=reply_markup,
            )
    else:
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=txt.choose_date(),
            reply_markup=reply_markup,
        )
    await state.set_state(AddBookingStates.selecting_date)


@router.callback_query(StateFilter(AddBookingStates.selecting_date), SimpleCalendarCallback.filter())
async def pick_date(
    callback: CallbackQuery,
    callback_data: SimpleCalendarCallback,
    state: FSMContext,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="master_add_booking", step="pick_date")
    await track_callback_message(state, callback, bucket=ADD_BOOKING_BUCKET)
    if not await rate_limit_callback(callback, rate_limiter, name="master_add_booking:pick_date", ttl_sec=1):
        return

    selected, picked_date = await SimpleCalendar().process_selection(callback, callback_data)
    if not selected:
        return

    state_data = await _pick_date_state(state)
    if state_data is None:
        ev.warning("master_add_booking.state_invalid", reason="missing_master_data")
        await context_lost(callback, state, bucket=ADD_BOOKING_BUCKET, reason="missing_master_data")
        return
    master_id, master_tz_enum = state_data

    try:
        async with session_local() as session:
            allowed, picked_day, today_master, max_date = await _validate_picked_day_in_horizon(
                session,
                master_id=master_id,
                picked_date=picked_date,
                master_tz_enum=master_tz_enum,
            )
            if not allowed:
                await callback.answer(
                    text=txt.date_out_of_range(today=today_master, max_date=max_date),
                    show_alert=True,
                )
                await _restore_calendar(callback, state)
                return

            result = await GetMasterFreeSlots(session).execute(
                master_id=master_id,
                client_day=picked_day,
                client_tz=master_tz_enum,
            )
    except Exception as exc:
        await ev.aexception("master_add_booking.pick_date_failed", stage="use_case", exc=exc)
        await callback.answer(ASYNC_CTX_ERROR, show_alert=True)
        return

    slots = result.slots_utc
    if not slots:
        ev.info("master_add_booking.slots_result", outcome="no_slots", master_id=master_id, day=str(picked_day))
        # Keep the calendar visible and notify via callback.answer to avoid chat spam.
        await callback.answer(text=txt.no_slots(), show_alert=True)
        await _restore_calendar(callback, state)
        return

    ev.info(
        "master_add_booking.slots_result",
        outcome="slots",
        master_id=master_id,
        day=str(picked_day),
        slots=len(slots),
    )
    await state.update_data(slots=[dt.isoformat() for dt in slots], master_day=result.master_day.isoformat())
    await _edit_or_send(
        callback,
        state,
        text=txt.slots_title(day=picked_day),
        reply_markup=_build_slots_keyboard([to_zone(dt, master_tz_enum) for dt in slots]),
    )
    await state.set_state(AddBookingStates.selecting_slot)


@router.callback_query(StateFilter(AddBookingStates.selecting_slot), F.data.startswith("m:add_booking:slot:"))
async def pick_slot(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_add_booking", step="pick_slot")
    await track_callback_message(state, callback, bucket=ADD_BOOKING_BUCKET)

    _, _, _, index_str = callback.data.split(":", 3)
    try:
        index = int(index_str)
    except ValueError:
        ev.warning("master_add_booking.input_invalid", field="slot_index", reason="parse_error")
        await callback.answer(ASYNC_CTX_ERROR, show_alert=True)
        return

    data = await state.get_data()
    slots_iso: list[str] = data.get("slots", [])
    if not slots_iso or index < 0 or index >= len(slots_iso):
        ev.warning(
            "master_add_booking.state_invalid",
            reason="slot_out_of_range",
            index=index,
            slots_len=len(slots_iso),
        )
        await context_lost(callback, state, bucket=ADD_BOOKING_BUCKET, reason="slot_out_of_range")
        return

    slot_dt = datetime.fromisoformat(slots_iso[index])
    client = data.get("client", {})
    master_timezone = data.get("master_timezone")
    master_tz_enum = Timezone(master_timezone) if master_timezone else None
    slot_master_tz = to_zone(slot_dt, master_tz_enum) if master_tz_enum else slot_dt

    await state.update_data(selected_slot=slot_dt.isoformat())

    client_name_safe = html_escape(str(client.get("name") or ""))
    if callback.message is not None:
        ok = await safe_edit_text(
            callback.message,
            text=txt.confirm_booking(
                client_name=client_name_safe,
                slot_str=slot_master_tz.strftime("%d.%m.%Y %H:%M"),
            ),
            reply_markup=_build_confirm_keyboard(),
            ev=ev,
            event="master_add_booking.edit_failed",
        )
        if not ok:
            await _send_and_track(
                state=state,
                bot=callback.bot,
                chat_id=callback.from_user.id,
                text=txt.confirm_booking(
                    client_name=client_name_safe,
                    slot_str=slot_master_tz.strftime("%d.%m.%Y %H:%M"),
                ),
                reply_markup=_build_confirm_keyboard(),
            )
    else:
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=txt.confirm_booking(
                client_name=client_name_safe,
                slot_str=slot_master_tz.strftime("%d.%m.%Y %H:%M"),
            ),
            reply_markup=_build_confirm_keyboard(),
        )
    await state.set_state(AddBookingStates.confirm)


@router.callback_query(StateFilter(AddBookingStates.confirm), F.data == "m:add_booking:confirm")
async def confirm_booking(
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    rate_limiter: RateLimiter | None = None,
    admin_alerter: AdminAlerter | None = None,
) -> None:
    bind_log_context(flow="master_add_booking", step="confirm")
    await track_callback_message(state, callback, bucket=ADD_BOOKING_BUCKET)
    if not await rate_limit_callback(callback, rate_limiter, name="master_add_booking:confirm", ttl_sec=2):
        return

    data = await state.get_data()
    if data.get("confirm_in_progress"):
        ev.debug("master_add_booking.confirm_duplicate_click")
        await callback.answer()
        return
    await state.update_data(confirm_in_progress=True)
    if callback.message is not None:
        await safe_edit_reply_markup(
            callback.message,
            reply_markup=None,
            ev=ev,
            event="master_add_booking.confirm.disable_keyboard_failed",
        )

    confirm = _parse_confirm_input(data)
    if confirm is None:
        ev.warning("master_add_booking.state_invalid", reason="missing_confirm_data")
        await context_lost(callback, state, bucket=ADD_BOOKING_BUCKET, reason="missing_confirm_data")
        return

    try:
        result = await _create_booking(
            CreateMasterBookingRequest(
                master_id=confirm.master_id,
                client_id=int(confirm.client["id"]),
                start_at_utc=confirm.slot_dt,
            ),
            admin_alerter=admin_alerter,
        )
    except Exception:
        await callback.answer(ASYNC_CTX_ERROR, show_alert=True)
        await _reset_add_booking(state, callback.bot)
        return

    ev.info(
        "master_add_booking.confirm_result",
        ok=bool(result.ok),
        error=str(result.error.value) if result.error else None,
        booking_id=result.booking.id if result.booking else None,
        master_id=confirm.master_id,
        client_id=confirm.client.get("id"),
    )

    await _handle_confirm_result(callback, state, notifier, confirm=confirm, result=result)


@router.callback_query(
    StateFilter(
        AddBookingStates.search_client,
        AddBookingStates.selecting_date,
        AddBookingStates.selecting_slot,
        AddBookingStates.confirm,
    ),
    F.data == "m:add_booking:cancel",
)
async def cancel(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_add_booking", step="cancel")
    ev.info("master_add_booking.cancelled")
    await callback.answer(txt.cancel_alert(), show_alert=True)
    await cleanup_messages(state, callback.bot, bucket=ADD_BOOKING_BUCKET)
    await state.clear()
