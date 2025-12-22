import logging
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram_calendar import SimpleCalendar, SimpleCalendarCallback

from src.core.sa import active_session, session_local
from src.datetime_utils import get_timezone, to_zone
from src.handlers.client.messages import CLIENT_NOT_FOUND_MESSAGE
from src.notifications import BookingContext, NotificationEvent, RecipientKind
from src.notifications.context import LimitsContext
from src.notifications.notifier import NotificationRequest, Notifier
from src.notifications.policy import NotificationFacts
from src.repositories import ClientNotFound, ClientRepository
from src.schemas import Master
from src.schemas.enums import Timezone
from src.texts.buttons import btn_cancel, btn_confirm, btn_decline
from src.texts.client_booking import (
    available_dates,
    booking_cancelled,
    booking_limit_reached,
    booking_not_saved,
    choose_date,
    choose_master,
    choose_time,
    confirm_details,
    done,
    incorrect_slot,
    no_available_slots,
    no_masters,
    slot_not_available,
    state_broken_alert,
)
from src.use_cases.create_client_booking import (
    CreateClientBooking,
    CreateClientBookingError,
    CreateClientBookingRequest,
)
from src.use_cases.entitlements import EntitlementsService
from src.use_cases.master_free_slots import GetMasterFreeSlots
from src.utils import answer_tracked, cleanup_messages, track_callback_message, track_message

router = Router(name=__name__)
logger = logging.getLogger(__name__)

BOOKING_BUCKET = "client_booking"


class ClientBooking(StatesGroup):
    selecting_master = State()
    selecting_date = State()
    selecting_slot = State()
    confirm = State()


def build_masters_keyboard(masters: list[Master]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for master in masters:
        rows.append([
            InlineKeyboardButton(text=master.name, callback_data=f"book:master:{master.id}"),
        ])
    rows.append([InlineKeyboardButton(text=btn_cancel(), callback_data="book:cancel_flow")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_slots_keyboard(slots: list[datetime]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for index, slot_dt in enumerate(slots):
        label = slot_dt.strftime("%H:%M")
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"book:slot:{index}"),
        ])

    rows.append([InlineKeyboardButton(text=btn_cancel(), callback_data="book:cancel_flow")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=btn_confirm(), callback_data="book:confirm"),
                InlineKeyboardButton(text=btn_cancel(), callback_data="book:cancel"),
            ],
        ],
    )


def build_master_booking_review_keyboard(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=btn_confirm(), callback_data=f"m:booking:{booking_id}:confirm"),
                InlineKeyboardButton(text=btn_decline(), callback_data=f"m:booking:{booking_id}:decline"),
            ],
        ],
    )


async def start_client_add_booking(message: Message, state: FSMContext) -> None:
    await track_message(state, message, bucket=BOOKING_BUCKET)
    telegram_id = message.from_user.id
    async with session_local() as session:
        repo = ClientRepository(session)
        try:
            client = await repo.get_details_by_telegram_id(telegram_id)
        except ClientNotFound:
            await message.answer(CLIENT_NOT_FOUND_MESSAGE)
            return
        masters = client.masters

    if not masters:
        await message.answer(no_masters())
        return

    await state.update_data(
        client_id=client.id,
        client_timezone=client.timezone,
        client_name=client.name,
    )

    if len(masters) == 1:
        master = masters[0]
        await start_booking_for_master(message, state, master.id)
        return

    await answer_tracked(
        message,
        state,
        text=choose_master(),
        reply_markup=build_masters_keyboard(masters),
        bucket=BOOKING_BUCKET,
    )
    await state.set_state(ClientBooking.selecting_master)


@router.callback_query(
    StateFilter(ClientBooking.selecting_master),
    F.data.startswith("book:master:"),
)
async def booking_select_master(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await track_callback_message(state, callback, bucket=BOOKING_BUCKET)

    _, _, master_id = callback.data.split(":")
    master_id = int(master_id)
    await start_booking_for_master(callback.message, state, master_id)


async def start_booking_for_master(
    message: Message,
    state: FSMContext,
    master_id: int,
) -> None:
    await state.update_data(master_id=master_id)

    async with session_local() as session:
        entitlements = EntitlementsService(session)
        check = await entitlements.can_create_booking(master_id=master_id)
        if not check.allowed:
            await cleanup_messages(state, message.bot, bucket=BOOKING_BUCKET)
            await state.clear()
            await message.answer(booking_limit_reached())
            return

    calendar = SimpleCalendar()
    reply_markup = await calendar.start_calendar()

    await answer_tracked(
        message,
        state,
        text=choose_date(),
        reply_markup=reply_markup,
        bucket=BOOKING_BUCKET,
    )
    await state.set_state(ClientBooking.selecting_date)


@router.callback_query(
    StateFilter(ClientBooking.selecting_date),
    SimpleCalendarCallback.filter(),
)
async def process_booking_calendar(
    callback: CallbackQuery,
    callback_data: SimpleCalendarCallback,
    state: FSMContext,
) -> None:
    await track_callback_message(state, callback, bucket=BOOKING_BUCKET)

    calendar = SimpleCalendar()
    selected, picked_date = await calendar.process_selection(callback, callback_data)
    if not selected:
        return

    data = await state.get_data()
    master_id = data.get("master_id")
    client_timezone: Timezone = data.get("client_timezone")
    if master_id is None or client_timezone is None:
        await callback.answer(state_broken_alert(), show_alert=True)
        return

    client_tz_info = get_timezone(str(client_timezone.value))
    client_day = picked_date.date() if picked_date.tzinfo is None \
        else picked_date.astimezone(client_tz_info).date()

    today_client = datetime.now(tz=client_tz_info).date()
    min_date = today_client + timedelta(days=1)
    async with session_local() as session:
        entitlements = EntitlementsService(session)
        horizon_days = await entitlements.max_booking_horizon_days(master_id=master_id)
        max_date = today_client + timedelta(days=horizon_days)
        if not (min_date <= client_day <= max_date):
            await callback.answer(
                text=available_dates(min_date=min_date, max_date=max_date),
                show_alert=True,
            )
            return

        await callback.answer()
        use_case = GetMasterFreeSlots(session)
        result = await use_case.execute(
            master_id=master_id, client_day=client_day, client_tz=client_timezone,
        )

    if not result.slots_utc:
        await callback.message.edit_text(no_available_slots())
        return

    slots_iso = [dt.isoformat() for dt in result.slots_utc]
    await state.update_data(
        booking_slots_utc=slots_iso,
        client_day=client_day.isoformat(),
    )

    await callback.message.edit_text(
        text=choose_time(client_day=client_day),
        reply_markup=build_slots_keyboard(result.slots_for_client),
    )
    await state.set_state(ClientBooking.selecting_slot)


@router.callback_query(
    StateFilter(ClientBooking.selecting_slot),
    F.data.startswith("book:slot:"),
)
async def booking_select_slot(callback: CallbackQuery, state: FSMContext) -> None:
    await track_callback_message(state, callback, bucket=BOOKING_BUCKET)

    _, _, index = callback.data.split(":")
    index = int(index)

    data = await state.get_data()
    slots_iso: list[str] = data.get("booking_slots_utc", [])
    client_timezone: Timezone = data.get("client_timezone")

    if client_timezone is None or not slots_iso:
        await callback.answer(state_broken_alert(), show_alert=True)
        return

    if index < 0 or index >= len(slots_iso):
        await callback.answer(incorrect_slot(), show_alert=True)
        return

    await callback.answer()

    slot_dt_utc = datetime.fromisoformat(slots_iso[index])  # utc aware
    slot_dt_client = slot_dt_utc.astimezone(get_timezone(str(client_timezone.value)))

    await state.update_data(selected_slot_index=index)

    await callback.message.edit_text(
        text=confirm_details(slot_dt_client=slot_dt_client),
        reply_markup=build_confirm_keyboard(),
    )
    await state.set_state(ClientBooking.confirm)


@router.callback_query(
    StateFilter(ClientBooking.confirm),
    F.data == "book:cancel",
)
async def booking_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer(booking_not_saved())
    await cleanup_messages(state, callback.bot, bucket=BOOKING_BUCKET)
    await state.clear()
    await callback.message.answer(booking_cancelled())


@router.callback_query(
    StateFilter(
        ClientBooking.selecting_master,
        ClientBooking.selecting_date,
        ClientBooking.selecting_slot,
        ClientBooking.confirm,
    ),
    F.data == "book:cancel_flow",
)
async def booking_cancel_flow(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer(booking_not_saved())
    await cleanup_messages(state, callback.bot, bucket=BOOKING_BUCKET)
    await state.clear()
    if callback.message:
        await callback.message.answer(booking_cancelled())


@router.callback_query(
    StateFilter(ClientBooking.confirm),
    F.data == "book:confirm",
)
async def booking_confirm(callback: CallbackQuery, state: FSMContext, notifier: Notifier) -> None:
    await track_callback_message(state, callback, bucket=BOOKING_BUCKET)

    data = await state.get_data()
    slots_iso: list[str] = data.get("booking_slots_utc", [])
    index = data.get("selected_slot_index")
    master_id = data.get("master_id")
    client_id = data.get("client_id")
    client_name = data.get("client_name", "")

    if master_id is None or index is None or client_id is None or not slots_iso:
        await callback.answer(state_broken_alert(), show_alert=True)
        return

    slot_dt_utc = datetime.fromisoformat(slots_iso[index])

    async with active_session() as session:
        use_case = CreateClientBooking(session)
        result = await use_case.execute(
            CreateClientBookingRequest(
                master_id=master_id,
                client_id=client_id,
                start_at_utc=slot_dt_utc,
            ),
        )

        if not result.ok:
            if result.error == CreateClientBookingError.QUOTA_EXCEEDED:
                await callback.answer(text=booking_limit_reached(), show_alert=True)
                return
            if result.error == CreateClientBookingError.SLOT_NOT_AVAILABLE:
                await callback.answer(text=slot_not_available(), show_alert=True)
                return
            await callback.answer(state_broken_alert(), show_alert=True)
            return

        booking = result.booking
        master = result.master
        if booking is None or master is None:
            await callback.answer(state_broken_alert(), show_alert=True)
            return
    await callback.answer()

    await cleanup_messages(state, callback.bot, bucket=BOOKING_BUCKET)
    await state.clear()
    await callback.message.answer(done())

    # Send a notification to the master
    slot_dt_master = to_zone(slot_dt_utc, master.timezone)
    slot_master_str = slot_dt_master.strftime("%d.%m.%Y %H:%M")
    await notifier.maybe_send(
        NotificationRequest(
            event=NotificationEvent.BOOKING_CREATED_PENDING,
            recipient=RecipientKind.MASTER,
            chat_id=master.telegram_id,
            context=BookingContext(
                booking_id=booking.id,
                master_name=master.name,
                client_name=client_name,
                slot_str=slot_master_str,
                duration_min=master.slot_size_min,
            ),
            reply_markup=build_master_booking_review_keyboard(booking.id),
        ),
    )
    if (
        result.warn_master_bookings_near_limit
        and (result.plan_is_pro is False)
        and result.usage is not None
        and result.bookings_limit is not None
    ):
        await notifier.maybe_send(
            NotificationRequest(
                event=NotificationEvent.WARNING_NEAR_BOOKINGS_LIMIT,
                recipient=RecipientKind.MASTER,
                chat_id=master.telegram_id,
                context=LimitsContext(
                    usage=result.usage,
                    bookings_limit=result.bookings_limit,
                ),
                facts=NotificationFacts(
                    event=NotificationEvent.WARNING_NEAR_BOOKINGS_LIMIT,
                    recipient=RecipientKind.MASTER,
                    chat_id=master.telegram_id,
                    plan_is_pro=result.plan_is_pro,
                ),
            ),
        )
