from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from html import escape as html_escape

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from src.core.sa import active_session, session_local
from src.datetime_utils import get_timezone
from src.filters.user_role import UserRole
from src.handlers.shared.flow import context_lost
from src.handlers.shared.guards import rate_limit_callback
from src.handlers.shared.ui import safe_edit_reply_markup, safe_edit_text
from src.notifications import NotificationEvent
from src.notifications.notifier import Notifier
from src.notifications.outbox import BookingClientOutboxNotification, maybe_enqueue_booking_client_notification
from src.notifications.policy import NotificationPolicy
from src.observability.alerts import AdminAlerter
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.paywall import build_paywall_keyboard
from src.plans import PRO_BOOKING_HORIZON_DAYS
from src.rate_limiter import RateLimiter
from src.repositories import BookingNotFound, MasterNotFound, MasterRepository
from src.repositories.booking import BookingRepository
from src.repositories.scheduled_notification import ScheduledNotificationRepository
from src.schemas.enums import BookingStatus, Timezone
from src.settings import get_settings
from src.texts import master_reschedule as txt, paywall as paywall_txt
from src.texts.buttons import btn_back, btn_cancel, btn_close, btn_confirm, btn_go_pro
from src.ui import month_calendar
from src.use_cases.entitlements import EntitlementsService
from src.use_cases.master_free_slots import GetMasterFreeSlots
from src.use_cases.reschedule_master_booking import (
    RescheduleMasterBooking,
    RescheduleMasterBookingError,
    RescheduleMasterBookingRequest,
    RescheduleMasterBookingResult,
)
from src.user_context import ActiveRole
from src.utils import cleanup_messages, track_message

ev = EventLogger(__name__)
router = Router(name=__name__)

CB_CONFIRM = "m:reschedule:confirm"
CB_CANCEL = "m:reschedule:cancel"
MONTH_CAL_PREFIX = "m:reschedule:mc"
_STATE_CAL_MONTH = "reschedule_calendar_month"
_FREE_BOOKING_HORIZON_DAYS = 7
RESCHEDULE_BUCKET = "master_reschedule"


class RescheduleStates(StatesGroup):
    selecting_date = State()
    selecting_slot = State()
    confirm = State()


@dataclass(frozen=True)
class _ConfirmMeta:
    booking_id: int
    new_start_at: datetime
    client_tg: int | None
    return_scope: str | None
    return_page: int | None


def _cb_slot(index: int) -> str:
    return f"m:reschedule:slot:{index}"


def _build_slots_keyboard(slots_local: list[datetime]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, slot in enumerate(slots_local):
        rows.append([InlineKeyboardButton(text=slot.strftime("%H:%M"), callback_data=_cb_slot(index))])
    rows.append([InlineKeyboardButton(text=btn_cancel(), callback_data=CB_CANCEL)])
    rows.append([InlineKeyboardButton(text=btn_close(), callback_data="m:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=btn_confirm(), callback_data=CB_CONFIRM),
                InlineKeyboardButton(text=btn_cancel(), callback_data=CB_CANCEL),
            ],
            [InlineKeyboardButton(text=btn_close(), callback_data="m:close")],
        ],
    )


async def _reset_reschedule(state: FSMContext, bot) -> None:
    await cleanup_messages(state, bot, bucket=RESCHEDULE_BUCKET)
    await state.clear()


async def _send_and_track(
    *,
    state: FSMContext,
    bot,
    chat_id: int,
    text: str,
    reply_markup=None,
) -> None:
    msg = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode="HTML")
    await track_message(state, msg, bucket=RESCHEDULE_BUCKET)


def _calendar_prompt_text() -> str:
    return "Выберите дату 📅\nДоступно: до 7 дней (Free) / до 60 дней (Pro)"


def _calendar_paywall_text() -> str:
    return "Даты дальше 7 дней доступны в Pro 🔒\n\nPro даёт запись до 60 дней вперёд."


def _month_ref_from_state(data: dict, *, today: date) -> month_calendar.MonthRef:
    raw = data.get(_STATE_CAL_MONTH)
    parsed = month_calendar.parse_month(str(raw)) if raw else None
    return parsed or month_calendar.MonthRef(year=int(today.year), month=int(today.month))


async def _calendar_limits(state: FSMContext) -> month_calendar.CalendarLimits | None:
    data = await state.get_data()
    state_data = _pick_date_state(data)
    if state_data is None:
        return None
    _booking_id, master_id, master_tz_name = state_data

    master_tz = get_timezone(master_tz_name)
    today_master = datetime.now(tz=master_tz).date()
    horizon_days = await _fetch_horizon_days(master_id)
    max_date = today_master + timedelta(days=int(horizon_days))
    pro_max_date = today_master + timedelta(days=PRO_BOOKING_HORIZON_DAYS)
    plan_is_pro = int(horizon_days) > _FREE_BOOKING_HORIZON_DAYS
    return month_calendar.CalendarLimits(
        today=today_master,
        min_date=today_master,
        max_date=max_date,
        pro_max_date=pro_max_date,
        plan_is_pro=plan_is_pro,
    )


async def _calendar_markup(state: FSMContext, *, month: month_calendar.MonthRef | None = None) -> InlineKeyboardMarkup:
    data = await state.get_data()
    limits = await _calendar_limits(state)
    if limits is None:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=btn_cancel(), callback_data=CB_CANCEL)]],
        )

    month_ref = month or _month_ref_from_state(data, today=limits.today)
    controls = month_calendar.CalendarControls(
        cancel_text=btn_cancel(),
        cancel_callback_data=CB_CANCEL,
        show_pro_button=True,
    )
    await state.update_data(_STATE_CAL_MONTH=f"{int(month_ref.year):04d}{int(month_ref.month):02d}")
    markup = month_calendar.build(prefix=MONTH_CAL_PREFIX, month=month_ref, limits=limits, controls=controls)
    markup.inline_keyboard.append([InlineKeyboardButton(text=btn_close(), callback_data="m:close")])
    return markup


async def _restore_calendar(callback: CallbackQuery, state: FSMContext) -> None:
    reply_markup = await _calendar_markup(state)
    if callback.message is not None:
        ok = await safe_edit_text(
            callback.message,
            text=_calendar_prompt_text(),
            reply_markup=reply_markup,
            ev=ev,
            event="master_reschedule.edit_failed",
        )
        if ok:
            return
    await _send_and_track(
        state=state,
        bot=callback.bot,
        chat_id=callback.from_user.id,
        text=_calendar_prompt_text(),
        reply_markup=reply_markup,
    )


async def _show_slots_list(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    picked_day: date,
    slots_local: list[datetime],
) -> None:
    text = txt.slots_title(day=picked_day)
    reply_markup = _build_slots_keyboard(slots_local)
    if callback.message is not None:
        ok = await safe_edit_text(
            callback.message,
            text=text,
            reply_markup=reply_markup,
            ev=ev,
            event="master_reschedule.edit_failed",
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


async def _load_booking_for_start(*, telegram_id: int, booking_id: int):
    try:
        async with session_local() as session:
            master_repo = MasterRepository(session)
            master = await master_repo.get_by_telegram_id(telegram_id)
            entitlements = EntitlementsService(session)
            plan = await entitlements.get_plan(master_id=master.id)
            if not plan.is_pro:
                return None, txt.pro_only(), {"reason": "pro_required", "master_id": master.id}, None

            booking_repo = BookingRepository(session)
            booking = await booking_repo.get_for_review(booking_id)
            return booking, None, None, None
    except MasterNotFound:
        return None, txt.broken_state(), {"reason": "master_not_found"}, None
    except BookingNotFound:
        return None, txt.update_failed(), {"reason": "booking_not_found", "booking_id": booking_id}, None
    except Exception as exc:
        return None, txt.update_failed(), {"reason": "load_failed"}, exc


def _deny_start_reason(booking, *, telegram_id: int) -> tuple[str | None, dict | None]:
    if booking.master.telegram_id != telegram_id:
        return txt.not_your_booking(), {"reason": "not_your_booking", "booking_id": booking.id}
    if booking.status not in BookingStatus.active():
        return (
            txt.not_reschedulable(),
            {"reason": "not_reschedulable", "booking_id": booking.id, "status": str(booking.status)},
        )
    if booking.start_at <= datetime.now(UTC):
        return txt.past_booking(), {"reason": "past_booking", "booking_id": booking.id}
    return None, None


def _pick_date_state(data: dict) -> tuple[int, int, str] | None:
    booking_id = data.get("reschedule_booking_id")
    master_id = data.get("reschedule_master_id")
    master_tz_name = data.get("reschedule_master_tz")
    if booking_id is None or master_id is None or not master_tz_name:
        return None
    try:
        return int(booking_id), int(master_id), str(master_tz_name)
    except (TypeError, ValueError):
        return None


async def _fetch_horizon_days(master_id: int) -> int:
    async with session_local() as session:
        entitlements = EntitlementsService(session)
        return int(await entitlements.max_booking_horizon_days(master_id=master_id))


async def _validate_picked_day_in_horizon(
    *,
    master_id: int,
    picked_day: date,
    master_tz,
) -> tuple[bool, date, date]:
    horizon_days = await _fetch_horizon_days(master_id)
    today_master = datetime.now(tz=master_tz).date()
    max_day = today_master + timedelta(days=horizon_days)
    return today_master <= picked_day <= max_day, today_master, max_day


async def _fetch_free_slots_for_day(
    *,
    master_id: int,
    master_day,
    master_tz: Timezone,
    exclude_booking_id: int,
):
    async with session_local() as session:
        use_case = GetMasterFreeSlots(session)
        return await use_case.execute(
            master_id=master_id,
            client_day=master_day,
            client_tz=master_tz,
            exclude_booking_id=exclude_booking_id,
        )


def _filter_out_original_slot(
    *,
    data: dict,
    slots_utc: list[datetime],
    slots_local: list[datetime],
    picked_day,
    master_tz,
) -> tuple[list[datetime], list[datetime]]:
    original_start_iso = data.get("reschedule_original_start_at")
    if not original_start_iso:
        return slots_utc, slots_local
    try:
        original_start_utc = datetime.fromisoformat(str(original_start_iso)).astimezone(UTC)
    except ValueError:
        return slots_utc, slots_local

    if original_start_utc.astimezone(master_tz).date() != picked_day:
        return slots_utc, slots_local

    filtered_pairs = [
        (slot_utc, slot_local)
        for slot_utc, slot_local in zip(slots_utc, slots_local, strict=False)
        if slot_utc != original_start_utc
    ]
    filtered_slots_utc = [slot_utc for slot_utc, _slot_local in filtered_pairs]
    filtered_slots_local = [_slot_local for _slot_utc, _slot_local in filtered_pairs]
    return filtered_slots_utc, filtered_slots_local


def _confirm_state(data: dict) -> tuple[int, datetime, int | None, str | None, int | None] | None:
    booking_id = data.get("reschedule_booking_id")
    slot_iso = data.get("reschedule_selected_slot")
    client_tg = data.get("reschedule_client_tg")
    return_scope = data.get("reschedule_scope")
    return_page = data.get("reschedule_page")
    if booking_id is None or slot_iso is None:
        return None
    try:
        booking_id_int = int(booking_id)
        new_start_at = datetime.fromisoformat(str(slot_iso))
        client_tg_int = int(client_tg) if client_tg is not None else None
        return_page_int = int(return_page) if return_page is not None else None
        return (
            booking_id_int,
            new_start_at,
            client_tg_int,
            str(return_scope) if return_scope else None,
            return_page_int,
        )
    except (TypeError, ValueError):
        return None


async def _confirm_error_slot_taken(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer(txt.slot_taken(), show_alert=True)
    await state.update_data(confirm_in_progress=False)
    await _restore_calendar(callback, state)
    await state.set_state(RescheduleStates.selecting_date)


async def _confirm_error_reset(callback: CallbackQuery, state: FSMContext, *, text: str) -> None:
    await callback.answer(text, show_alert=True)
    await _reset_reschedule(state, callback.bot)


async def _confirm_error_pro_required(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    meta = _confirm_state(data)
    if callback.message is None or meta is None:
        await _confirm_error_reset(callback, state, text=txt.pro_only())
        return

    booking_id, _new_start_at, _client_tg, scope, page = meta
    back_cb = f"m:b:{booking_id}:s:{scope}:p:{page}" if scope and page is not None else "paywall:close"

    await callback.answer()
    await safe_edit_text(
        callback.message,
        text=paywall_txt.reschedule_pro_only(),
        reply_markup=build_paywall_keyboard(
            contact=get_settings().billing.contact,
            upgrade_text=btn_go_pro(),
            back_text=btn_back(),
            back_callback_data=back_cb,
            upgrade_callback_data="billing:pro:start",
            force_upgrade_callback=True,
        ),
        parse_mode="HTML",
        ev=ev,
        event="master_reschedule.paywall_edit_failed",
    )
    await _reset_reschedule(state, callback.bot)


async def _confirm_error_forbidden(callback: CallbackQuery, state: FSMContext) -> None:
    await _confirm_error_reset(callback, state, text=txt.not_your_booking())


async def _confirm_error_not_reschedulable(callback: CallbackQuery, state: FSMContext) -> None:
    await _confirm_error_reset(callback, state, text=txt.not_reschedulable())


async def _confirm_error_same_slot(callback: CallbackQuery, state: FSMContext) -> None:
    await _confirm_error_reset(callback, state, text=txt.same_slot())


async def _disable_callback_keyboard(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    await safe_edit_reply_markup(
        callback.message,
        reply_markup=None,
        ev=ev,
        event="master_reschedule.confirm.disable_keyboard_failed",
    )


async def _execute_reschedule(
    *,
    callback: CallbackQuery,
    booking_id: int,
    new_start_at: datetime,
    admin_alerter: AdminAlerter | None,
):
    try:
        async with active_session() as session:
            return await RescheduleMasterBooking(session).execute(
                RescheduleMasterBookingRequest(
                    master_telegram_id=callback.from_user.id,
                    booking_id=booking_id,
                    new_start_at_utc=new_start_at,
                ),
            )
    except Exception as exc:
        await ev.aexception(
            "master_reschedule.confirm_failed",
            stage="use_case",
            exc=exc,
            admin_alerter=admin_alerter,
        )
        return RescheduleMasterBookingResult(ok=False)


async def _apply_confirm_result(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    result: RescheduleMasterBookingResult,
    meta: _ConfirmMeta,
) -> None:
    ev.info(
        "master_reschedule.confirm_result",
        ok=bool(result.ok),
        error=str(result.error.value) if result.error else None,
        booking_id=meta.booking_id,
    )

    if not result.ok:
        await _handle_confirm_error(error=result.error, callback=callback, state=state)
        if result.error != RescheduleMasterBookingError.SLOT_NOT_AVAILABLE:
            await _return_to_schedule(callback, scope=meta.return_scope, page=meta.return_page)
        return

    booking = result.booking
    if booking is None:
        ev.warning("master_reschedule.state_invalid", reason="missing_booking_result")
        await context_lost(callback, state, bucket=RESCHEDULE_BUCKET, reason="missing_booking_result")
        return

    await callback.answer(txt.updated(), show_alert=True)

    if meta.client_tg is not None:
        await _notify_client_about_reschedule(
            callback=callback,
            booking=booking,
            new_start_at=meta.new_start_at,
            client_tg=meta.client_tg,
            plan_is_pro=result.plan_is_pro,
            policy=notifier.policy,
        )

    await _reset_reschedule(state, callback.bot)
    await _return_to_schedule(callback, scope=meta.return_scope, page=meta.return_page)


async def _handle_confirm_error(
    *,
    error: RescheduleMasterBookingError | None,
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    handlers = {
        RescheduleMasterBookingError.SLOT_NOT_AVAILABLE: _confirm_error_slot_taken,
        RescheduleMasterBookingError.PRO_REQUIRED: _confirm_error_pro_required,
        RescheduleMasterBookingError.FORBIDDEN: _confirm_error_forbidden,
        RescheduleMasterBookingError.NOT_RESCHEDULABLE: _confirm_error_not_reschedulable,
        RescheduleMasterBookingError.PAST_BOOKING: _confirm_error_not_reschedulable,
        RescheduleMasterBookingError.SAME_SLOT: _confirm_error_same_slot,
    }
    handler = handlers.get(error)
    if handler is None:
        await _confirm_error_reset(callback, state, text=txt.update_failed())
        return
    await handler(callback, state)


async def _notify_client_about_reschedule(
    *,
    callback: CallbackQuery,
    booking,
    new_start_at: datetime,
    client_tg: int,
    plan_is_pro: bool | None,
    policy: NotificationPolicy,
) -> None:
    async with active_session() as session:
        await maybe_enqueue_booking_client_notification(
            policy=policy,
            outbox=ScheduledNotificationRepository(session),
            request=BookingClientOutboxNotification(
                event=NotificationEvent.BOOKING_RESCHEDULED_BY_MASTER,
                chat_id=int(client_tg),
                booking_id=int(booking.id),
                booking_start_at=new_start_at,
                now_utc=datetime.now(UTC),
                plan_is_pro=plan_is_pro,
                master_notify_clients=bool(getattr(booking.master, "notify_clients", True)),
                client_notifications_enabled=bool(getattr(booking.client, "notifications_enabled", True)),
            ),
        )


async def _return_to_schedule(callback: CallbackQuery, *, scope: str | None, page: int | None) -> None:
    if callback.message is None or not scope or page is None:
        return
    from src.handlers.master.schedule import Scope, _send_schedule

    await _send_schedule(callback, scope=Scope(scope), page=page)


async def start_reschedule(
    callback: CallbackQuery,
    state: FSMContext,
    rate_limiter: RateLimiter | None = None,
    *,
    booking_id: int,
    scope,
    page: int,
) -> None:
    """
    Entrypoint called from schedule action handler.
    `scope` is expected to be src.handlers.master.schedule.Scope.
    """
    bind_log_context(flow="master_reschedule", step="start")
    if not await rate_limit_callback(
        callback,
        rate_limiter,
        name="master_reschedule:start",
        ttl_sec=2,
        booking_id=booking_id,
    ):
        return
    ev.info("master_reschedule.start", booking_id=booking_id)

    booking, deny_text, deny_meta, deny_exc = await _load_booking_for_start(
        telegram_id=callback.from_user.id,
        booking_id=booking_id,
    )
    if deny_text is not None:
        if deny_exc is not None:
            await ev.aexception("master_reschedule.start_failed", stage="load", exc=deny_exc)
        else:
            ev.info("master_reschedule.start_denied", **(deny_meta or {}))
        if (deny_meta or {}).get("reason") == "pro_required":
            back_cb = f"m:b:{booking_id}:s:{getattr(scope, 'value', str(scope))}:p:{page}"
            await callback.answer()
            if callback.message is not None:
                await safe_edit_text(
                    callback.message,
                    text=paywall_txt.reschedule_pro_only(),
                    reply_markup=build_paywall_keyboard(
                        contact=get_settings().billing.contact,
                        upgrade_text=btn_go_pro(),
                        back_text=btn_back(),
                        back_callback_data=back_cb,
                        upgrade_callback_data="billing:pro:start",
                        force_upgrade_callback=True,
                    ),
                    parse_mode="HTML",
                    ev=ev,
                    event="master_reschedule.paywall_edit_failed",
                )
            else:
                await callback.bot.send_message(
                    chat_id=callback.from_user.id,
                    text=paywall_txt.reschedule_pro_only(),
                    reply_markup=build_paywall_keyboard(
                        contact=get_settings().billing.contact,
                        upgrade_text=btn_go_pro(),
                        back_text=btn_back(),
                        back_callback_data=back_cb,
                        upgrade_callback_data="billing:pro:start",
                        force_upgrade_callback=True,
                    ),
                    parse_mode="HTML",
                )
            return
        await callback.answer(deny_text, show_alert=True)
        return

    deny_text, deny_meta = _deny_start_reason(booking, telegram_id=callback.from_user.id)
    if deny_text is not None:
        ev.info("master_reschedule.start_denied", **(deny_meta or {}))
        await callback.answer(deny_text, show_alert=True)
        return

    await _reset_reschedule(state, callback.bot)
    await state.update_data(
        reschedule_booking_id=booking_id,
        reschedule_scope=getattr(scope, "value", str(scope)),
        reschedule_page=page,
        reschedule_master_id=booking.master.id,
        reschedule_master_tz=str(booking.master.timezone.value),
        reschedule_original_start_at=booking.start_at.astimezone(UTC).isoformat(),
        reschedule_client_name=booking.client.name,
        reschedule_client_tg=booking.client.telegram_id,
        confirm_in_progress=False,
    )

    await _restore_calendar(callback, state)
    await state.set_state(RescheduleStates.selecting_date)
    await callback.answer()


async def _mc_context(state: FSMContext) -> tuple[month_calendar.CalendarLimits, month_calendar.MonthRef, dict] | None:
    limits = await _calendar_limits(state)
    if limits is None:
        return None
    data = await state.get_data()
    return limits, _month_ref_from_state(data, today=limits.today), data


async def _mc_show_month(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    month_ref: month_calendar.MonthRef,
    event: str,
) -> None:
    await callback.answer()
    if callback.message is None:
        return
    await safe_edit_text(
        callback.message,
        text=_calendar_prompt_text(),
        reply_markup=await _calendar_markup(state, month=month_ref),
        ev=ev,
        event=event,
    )


async def _mc_show_paywall(callback: CallbackQuery, *, shown_month: month_calendar.MonthRef) -> None:
    await callback.answer()
    if callback.message is None:
        return
    await safe_edit_text(
        callback.message,
        text=_calendar_paywall_text(),
        reply_markup=build_paywall_keyboard(
            contact=get_settings().billing.contact,
            upgrade_text=btn_go_pro(),
            back_text=btn_back(),
            back_callback_data=month_calendar.cb_month(
                MONTH_CAL_PREFIX,
                year=shown_month.year,
                month=shown_month.month,
            ),
            upgrade_callback_data="billing:pro:start",
            force_upgrade_callback=True,
        ),
        ev=ev,
        event="master_reschedule.paywall_edit_failed",
    )


async def _mc_fetch_slots_for_picked_day(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    data: dict,
    state_data: tuple[int, int, str],
    picked_day: date,
) -> tuple[list[datetime], list[datetime]] | None:
    booking_id, master_id, master_tz_name = state_data
    master_tz = get_timezone(master_tz_name)
    try:
        allowed, today_master, max_day = await _validate_picked_day_in_horizon(
            master_id=master_id,
            picked_day=picked_day,
            master_tz=master_tz,
        )
        if not allowed:
            await callback.answer(
                text=txt.date_out_of_range(today=today_master, max_day=max_day),
                show_alert=True,
            )
            await _restore_calendar(callback, state)
            return None

        result = await _fetch_free_slots_for_day(
            master_id=master_id,
            master_day=picked_day,
            master_tz=Timezone(master_tz_name),
            exclude_booking_id=booking_id,
        )
    except Exception as exc:
        await ev.aexception("master_reschedule.pick_date_failed", stage="use_case", exc=exc)
        await callback.answer(txt.update_failed(), show_alert=True)
        await _restore_calendar(callback, state)
        return None

    slots_utc, slots_local = _filter_out_original_slot(
        data=data,
        slots_utc=list(result.slots_utc),
        slots_local=list(result.slots_for_client),
        picked_day=picked_day,
        master_tz=master_tz,
    )
    if not slots_utc:
        ev.info("master_reschedule.slots_result", outcome="no_slots", master_id=master_id, day=str(picked_day))
        await callback.answer(text=txt.no_slots(), show_alert=True)
        month_ref = month_calendar.MonthRef(year=int(picked_day.year), month=int(picked_day.month))
        await _mc_show_month(callback, state, month_ref=month_ref, event="master_reschedule.edit_failed")
        return None

    return slots_utc, slots_local


async def _mc_handle_day(callback: CallbackQuery, state: FSMContext, *, arg: str | None) -> None:
    picked_day = month_calendar.parse_day(arg)
    if picked_day is None:
        await callback.answer(txt.update_failed(), show_alert=True)
        return

    data = await state.get_data()
    state_data = _pick_date_state(data)
    if state_data is None:
        ev.warning("master_reschedule.state_invalid", reason="missing_state", stage="pick_date")
        await context_lost(callback, state, bucket=RESCHEDULE_BUCKET, reason="missing_state")
        return
    booking_id, master_id, master_tz_name = state_data

    slots = await _mc_fetch_slots_for_picked_day(
        callback,
        state,
        data=data,
        state_data=state_data,
        picked_day=picked_day,
    )
    if slots is None:
        return
    slots_utc, slots_local = slots

    await state.update_data(
        reschedule_day=picked_day.isoformat(),
        reschedule_slots=[dt.isoformat() for dt in slots_utc],
    )
    await _show_slots_list(callback, state, picked_day=picked_day, slots_local=slots_local)
    await state.set_state(RescheduleStates.selecting_slot)
    await callback.answer()


async def _mc_dispatch(callback: CallbackQuery, state: FSMContext) -> None:
    action, arg = month_calendar.parse(MONTH_CAL_PREFIX, callback.data)
    if action in {"invalid", "noop"}:
        await callback.answer()
        return

    ctx = await _mc_context(state)
    if ctx is None:
        await callback.answer(txt.update_failed(), show_alert=True)
        return
    limits, shown_month, _data = ctx

    if action == "today":
        await _mc_show_month(
            callback,
            state,
            month_ref=month_calendar.MonthRef(year=int(limits.today.year), month=int(limits.today.month)),
            event="master_reschedule.edit_failed",
        )
        return

    if action == "m":
        await _mc_show_month(
            callback,
            state,
            month_ref=month_calendar.parse_month(arg) or shown_month,
            event="master_reschedule.edit_failed",
        )
        return

    if action in {"l", "pro"}:
        await _mc_show_paywall(callback, shown_month=shown_month)
        return

    if action == "d":
        await _mc_handle_day(callback, state, arg=arg)
        return

    await callback.answer(txt.update_failed(), show_alert=True)


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(RescheduleStates.selecting_date),
    F.data.startswith(f"{MONTH_CAL_PREFIX}:"),
)
async def pick_date(callback: CallbackQuery, state: FSMContext, rate_limiter: RateLimiter | None = None) -> None:
    bind_log_context(flow="master_reschedule", step="pick_date")
    if not await rate_limit_callback(callback, rate_limiter, name="master_reschedule:pick_date", ttl_sec=1):
        return
    await _mc_dispatch(callback, state)


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(RescheduleStates.selecting_slot),
    F.data.startswith("m:reschedule:slot:"),
)
async def pick_slot(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_reschedule", step="pick_slot")
    if callback.data is None:
        ev.warning("master_reschedule.input_invalid", field="callback_data", reason="missing")
        await callback.answer(txt.broken_state(), show_alert=True)
        return

    parts = callback.data.split(":", 3)
    if len(parts) != 4 or parts[0] != "m" or parts[1] != "reschedule" or parts[2] != "slot":  # noqa: PLR2004
        ev.warning("master_reschedule.input_invalid", field="slot_callback", reason="unexpected_format")
        await callback.answer(txt.broken_state(), show_alert=True)
        return
    try:
        index = int(parts[3])
    except ValueError:
        ev.warning("master_reschedule.input_invalid", field="slot_index", reason="parse_error")
        await callback.answer(txt.broken_state(), show_alert=True)
        return

    data = await state.get_data()
    slots_iso: list[str] = data.get("reschedule_slots", [])
    master_tz_name = data.get("reschedule_master_tz")
    client_name = data.get("reschedule_client_name") or txt.client_fallback()
    if not slots_iso or master_tz_name is None or index < 0 or index >= len(slots_iso):
        ev.warning("master_reschedule.state_invalid", reason="slot_out_of_range", index=index, slots_len=len(slots_iso))
        await context_lost(callback, state, bucket=RESCHEDULE_BUCKET, reason="slot_out_of_range")
        return

    slot_utc = datetime.fromisoformat(slots_iso[index])
    slot_local = slot_utc.astimezone(get_timezone(master_tz_name))
    await state.update_data(reschedule_selected_slot=slot_utc.isoformat())

    if callback.message:
        client_name_safe = html_escape(str(client_name))
        ok = await safe_edit_text(
            callback.message,
            text=txt.confirm(
                client_name=client_name_safe,
                day=f"{slot_local:%d.%m.%Y}",
                time_str=f"{slot_local:%H:%M}",
            ),
            reply_markup=_build_confirm_keyboard(),
            ev=ev,
            event="master_reschedule.edit_failed",
        )
        if not ok:
            await _send_and_track(
                state=state,
                bot=callback.bot,
                chat_id=callback.from_user.id,
                text=txt.confirm(
                    client_name=client_name_safe,
                    day=f"{slot_local:%d.%m.%Y}",
                    time_str=f"{slot_local:%H:%M}",
                ),
                reply_markup=_build_confirm_keyboard(),
            )
    else:
        client_name_safe = html_escape(str(client_name))
        await _send_and_track(
            state=state,
            bot=callback.bot,
            chat_id=callback.from_user.id,
            text=txt.confirm(
                client_name=client_name_safe,
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
async def confirm(
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    rate_limiter: RateLimiter | None = None,
    admin_alerter: AdminAlerter | None = None,
) -> None:
    bind_log_context(flow="master_reschedule", step="confirm")
    data = await state.get_data()
    if not await rate_limit_callback(
        callback,
        rate_limiter,
        name="master_reschedule:confirm",
        ttl_sec=2,
    ):
        return
    if data.get("confirm_in_progress"):
        ev.debug("master_reschedule.confirm_duplicate_click")
        await callback.answer()
        return
    await state.update_data(confirm_in_progress=True)
    await _disable_callback_keyboard(callback)

    state_data = _confirm_state(data)
    if state_data is None:
        ev.warning("master_reschedule.state_invalid", reason="missing_confirm_data")
        await context_lost(callback, state, bucket=RESCHEDULE_BUCKET, reason="missing_confirm_data")
        return
    booking_id, new_start_at, client_tg, return_scope, return_page = state_data
    result = await _execute_reschedule(
        callback=callback,
        booking_id=booking_id,
        new_start_at=new_start_at,
        admin_alerter=admin_alerter,
    )
    await _apply_confirm_result(
        callback=callback,
        state=state,
        notifier=notifier,
        result=result,
        meta=_ConfirmMeta(
            booking_id=booking_id,
            new_start_at=new_start_at,
            client_tg=client_tg,
            return_scope=return_scope,
            return_page=return_page,
        ),
    )


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(RescheduleStates.selecting_date, RescheduleStates.selecting_slot, RescheduleStates.confirm),
    F.data == CB_CANCEL,
)
async def cancel(callback: CallbackQuery, state: FSMContext) -> None:
    from src.handlers.master.schedule import Scope, _send_schedule

    bind_log_context(flow="master_reschedule", step="cancel")
    data = await state.get_data()
    return_scope = data.get("reschedule_scope")
    return_page = data.get("reschedule_page")
    await _reset_reschedule(state, callback.bot)
    await callback.answer(txt.cancelled(), show_alert=True)
    if callback.message and return_scope and return_page:
        await _send_schedule(callback, scope=Scope(return_scope), page=int(return_page))
