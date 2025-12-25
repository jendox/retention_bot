from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram_calendar import SimpleCalendar, SimpleCalendarCallback
from sqlalchemy.exc import IntegrityError

from src.core.sa import active_session, session_local
from src.datetime_utils import get_timezone, to_zone, utc_range_for_master_day
from src.filters.user_role import UserRole
from src.notifications import BookingContext, NotificationEvent, NotificationService, RecipientKind
from src.repositories import MasterRepository
from src.repositories.booking import BookingRepository
from src.schedule import get_free_slots_for_date
from src.schemas.enums import BookingStatus, Timezone
from src.texts import master_reschedule as txt
from src.texts.buttons import btn_cancel, btn_cancel_booking, btn_confirm
from src.use_cases.entitlements import EntitlementsService
from src.user_context import ActiveRole

logger = logging.getLogger(__name__)
router = Router(name=__name__)

CB_CONFIRM = f"m:reschedule:confirm"
CB_CANCEL = f"m:reschedule:cancel"


class RescheduleStates(StatesGroup):
    selecting_date = State()
    selecting_slot = State()
    confirm = State()


def _cb_slot(index: int) -> str:
    return f"m:reschedule:slot:{index}"


def _build_slots_keyboard(slots_local: list[datetime]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, slot in enumerate(slots_local):
        rows.append([InlineKeyboardButton(text=slot.strftime("%H:%M"), callback_data=_cb_slot(index))])
    rows.append([InlineKeyboardButton(text=btn_cancel(), callback_data=CB_CANCEL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=btn_confirm(), callback_data=CB_CONFIRM),
                InlineKeyboardButton(text=btn_cancel(), callback_data=CB_CANCEL),
            ],
        ],
    )


async def start_reschedule(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    booking_id: int,
    scope,
    page: int,
) -> None:
    """
    Entrypoint called from schedule action handler.
    `scope` is expected to be src.handlers.master.schedule.Scope.
    """
    async with session_local() as session:
        master_repo = MasterRepository(session)
        master = await master_repo.get_by_telegram_id(callback.from_user.id)
        entitlements = EntitlementsService(session)
        plan = await entitlements.get_plan(master_id=master.id)
        if not plan.is_pro:
            await callback.answer(txt.pro_only(), show_alert=True)
            return

        booking_repo = BookingRepository(session)
        booking = await booking_repo.get_for_review(booking_id)

    if booking.master.telegram_id != callback.from_user.id:
        await callback.answer(txt.not_your_booking(), show_alert=True)
        return
    if booking.status not in BookingStatus.active():
        await callback.answer(txt.not_reschedulable(), show_alert=True)
        return
    if booking.start_at <= datetime.now(UTC):
        await callback.answer(txt.past_booking(), show_alert=True)
        return

    await state.clear()
    await state.update_data(
        reschedule_booking_id=booking_id,
        reschedule_scope=getattr(scope, "value", str(scope)),
        reschedule_page=page,
        reschedule_master_id=booking.master.id,
        reschedule_master_tz=str(booking.master.timezone.value),
        reschedule_client_name=booking.client.name,
        reschedule_client_tg=booking.client.telegram_id,
    )

    calendar = SimpleCalendar()
    reply_markup = await calendar.start_calendar()
    if callback.message:
        await callback.message.edit_text(txt.choose_new_date(), reply_markup=reply_markup)
    await state.set_state(RescheduleStates.selecting_date)
    await callback.answer()


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(RescheduleStates.selecting_date),
    SimpleCalendarCallback.filter(),
)
async def pick_date(callback: CallbackQuery, callback_data: SimpleCalendarCallback, state: FSMContext) -> None:
    calendar = SimpleCalendar()
    selected, picked_date = await calendar.process_selection(callback, callback_data)
    if not selected:
        return

    data = await state.get_data()
    booking_id = data.get("reschedule_booking_id")
    master_id = data.get("reschedule_master_id")
    master_tz_name = data.get("reschedule_master_tz")
    if booking_id is None or master_id is None or not master_tz_name:
        await callback.answer(txt.broken_state(), show_alert=True)
        await state.clear()
        return

    master_tz = get_timezone(master_tz_name)
    picked_day = picked_date.date() if picked_date.tzinfo is None else picked_date.astimezone(master_tz).date()

    async with session_local() as session:
        entitlements = EntitlementsService(session)
        horizon_days = await entitlements.max_booking_horizon_days(master_id=master_id)
    today_master = datetime.now(tz=master_tz).date()
    max_day = today_master + timedelta(days=horizon_days)
    if not (today_master <= picked_day <= max_day):
        await callback.answer(
            text=txt.date_out_of_range(today=today_master, max_day=max_day),
            show_alert=True,
        )
        return

    async with session_local() as session:
        master_repo = MasterRepository(session)
        booking_repo = BookingRepository(session)
        master = await master_repo.get_for_schedule_by_id(master_id)

        master_tz_enum = Timezone(master_tz_name)
        utc_range = utc_range_for_master_day(master_day=picked_day, master_tz=master_tz_enum)
        bookings = await booking_repo.get_for_master_in_range(
            master_id=master_id,
            start_at_utc=utc_range.start,
            end_at_utc=utc_range.end,
            statuses=BookingStatus.active(),
            load_clients=False,
        )
        bookings = [b for b in bookings if b.id != booking_id]

        slots_local = get_free_slots_for_date(master=master, target_date=picked_day, bookings=bookings)

    if not slots_local:
        await callback.answer()
        if callback.message:
            await callback.message.edit_text(txt.no_slots())
        return

    slots_utc = [dt.astimezone(UTC) for dt in slots_local]
    await state.update_data(
        reschedule_day=picked_day.isoformat(),
        reschedule_slots=[dt.isoformat() for dt in slots_utc],
    )

    if callback.message:
        await callback.message.edit_text(
            text=txt.slots_title(day=picked_day),
            reply_markup=_build_slots_keyboard(slots_local),
        )
    await state.set_state(RescheduleStates.selecting_slot)
    await callback.answer()


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(RescheduleStates.selecting_slot),
    F.data.startswith(f"m:reschedule:slot:"),
)
async def pick_slot(callback: CallbackQuery, state: FSMContext) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[0] != "m" or parts[1] != "r" or parts[2] != "slot":  # noqa: PLR2004
        await callback.answer(txt.broken_state(), show_alert=True)
        return
    try:
        index = int(parts[3])
    except ValueError:
        await callback.answer(txt.broken_state(), show_alert=True)
        return

    data = await state.get_data()
    slots_iso: list[str] = data.get("reschedule_slots", [])
    master_tz_name = data.get("reschedule_master_tz")
    client_name = data.get("reschedule_client_name") or txt.client_fallback()
    if not slots_iso or master_tz_name is None or index < 0 or index >= len(slots_iso):
        await callback.answer(txt.broken_state(), show_alert=True)
        return

    slot_utc = datetime.fromisoformat(slots_iso[index])
    slot_local = slot_utc.astimezone(get_timezone(master_tz_name))
    await state.update_data(reschedule_selected_slot=slot_utc.isoformat())

    if callback.message:
        await callback.message.edit_text(
            text=txt.confirm(
                client_name=client_name,
                day=f"{slot_local:%d.%m.%Y}",
                time_str=f"{slot_local:%H:%M}",
            ),
            reply_markup=_build_confirm_keyboard(),
        )
    await state.set_state(RescheduleStates.confirm)
    await callback.answer()


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(RescheduleStates.confirm),
    F.data == CB_CONFIRM,
)
async def confirm(callback: CallbackQuery, state: FSMContext) -> None:
    from src.handlers.master.schedule import Scope, _send_schedule

    data = await state.get_data()
    booking_id = data.get("reschedule_booking_id")
    master_id = data.get("reschedule_master_id")
    slot_iso = data.get("reschedule_selected_slot")
    master_tz_name = data.get("reschedule_master_tz")
    client_tg = data.get("reschedule_client_tg")
    return_scope = data.get("reschedule_scope")
    return_page = data.get("reschedule_page")

    if booking_id is None or master_id is None or slot_iso is None or not master_tz_name:
        await callback.answer(txt.broken_state(), show_alert=True)
        await state.clear()
        return

    new_start_at = datetime.fromisoformat(slot_iso)
    try:
        async with active_session() as session:
            booking_repo = BookingRepository(session)
            updated = await booking_repo.reschedule(booking_id=booking_id, master_id=master_id, start_at=new_start_at)
    except IntegrityError:
        await callback.answer(
            txt.slot_taken(),
            show_alert=True,
        )
        await state.set_state(RescheduleStates.selecting_date)
        calendar = SimpleCalendar()
        reply_markup = await calendar.start_calendar()
        if callback.message:
            await callback.message.edit_text(txt.choose_new_date(), reply_markup=reply_markup)
        return

    if not updated:
        await callback.answer(txt.update_failed(), show_alert=True)
        await state.clear()
        return

    await callback.answer(txt.updated(), show_alert=True)

    if client_tg:
        async with session_local() as session:
            booking_repo = BookingRepository(session)
            entitlements = EntitlementsService(session)
            booking = await booking_repo.get_for_review(booking_id)
            plan = await entitlements.get_plan(master_id=booking.master.id)

        allow_client_notifications = (
            plan.is_pro
            and bool(getattr(booking.master, "notify_clients", True))
            and bool(getattr(booking.client, "notifications_enabled", True))
        )
        if allow_client_notifications:
            slot_client = to_zone(new_start_at.astimezone(UTC), booking.client.timezone)

            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=btn_cancel_booking(), callback_data=f"c:booking:{booking.id}:cancel")],
                ],
            )

            notification = NotificationService(callback.bot)
            await notification.send_booking(
                event=NotificationEvent.BOOKING_RESCHEDULED_BY_MASTER,
                recipient=RecipientKind.CLIENT,
                chat_id=client_tg,
                context=BookingContext(
                    booking_id=booking.id,
                    master_name=booking.master.name,
                    client_name=booking.client.name,
                    slot_str=slot_client.strftime("%d.%m.%Y %H:%M"),
                    duration_min=booking.duration_min,
                ),
                reply_markup=reply_markup,
            )

    await state.clear()
    if callback.message and return_scope and return_page:
        await _send_schedule(callback, scope=Scope(return_scope), page=int(return_page))


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(RescheduleStates.selecting_date, RescheduleStates.selecting_slot, RescheduleStates.confirm),
    F.data == CB_CANCEL,
)
async def cancel(callback: CallbackQuery, state: FSMContext) -> None:
    from src.handlers.master.schedule import Scope, _send_schedule

    data = await state.get_data()
    return_scope = data.get("reschedule_scope")
    return_page = data.get("reschedule_page")
    await state.clear()
    await callback.answer(txt.cancelled(), show_alert=True)
    if callback.message and return_scope and return_page:
        await _send_schedule(callback, scope=Scope(return_scope), page=int(return_page))
