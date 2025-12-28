from __future__ import annotations

from datetime import UTC, datetime, time as time_type, timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery

from src.core.sa import active_session
from src.datetime_utils import get_timezone
from src.filters.user_role import UserRole
from src.handlers.shared.ui import safe_edit_reply_markup
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.repositories import MasterRepository
from src.repositories.booking import BookingRepository
from src.repositories.scheduled_notification import ScheduledNotificationRepository
from src.schemas.enums import AttendanceOutcome
from src.texts import common as common_txt
from src.use_cases.mark_booking_attendance import MarkBookingAttendance, MarkBookingAttendanceRequest
from src.user_context import ActiveRole

ev = EventLogger(__name__)
router = Router(name=__name__)

ATT_PREFIX = "m:att_rem:"


def _parse_booking_id(data: str, *, prefix: str) -> int | None:
    if not (data or "").startswith(prefix):
        return None
    try:
        return int((data or "").split(":")[-1])
    except Exception:
        return None


async def _hide_keyboard(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    await safe_edit_reply_markup(
        callback.message,
        reply_markup=None,
        ev=ev,
        event="attendance_reminder.hide_keyboard_failed",
    )


async def _mark_attendance(callback: CallbackQuery, *, booking_id: int, outcome: AttendanceOutcome) -> None:
    try:
        async with active_session() as session:
            result = await MarkBookingAttendance(session).execute(
                MarkBookingAttendanceRequest(
                    master_telegram_id=int(callback.from_user.id),
                    booking_id=int(booking_id),
                    outcome=outcome,
                ),
            )
            await ScheduledNotificationRepository(session).cancel_attendance_nudges_for_booking(
                booking_id=int(booking_id),
            )
    except Exception as exc:
        await ev.aexception("attendance_reminder.mark_failed", exc=exc, booking_id=int(booking_id))
        await callback.answer(common_txt.generic_error(), show_alert=True)
        return

    if result.ok or (result.error and result.error.value == "already_marked"):
        await callback.answer(common_txt.saved(), show_alert=False)
        await _hide_keyboard(callback)
        return

    await callback.answer(common_txt.generic_error(), show_alert=True)


async def _snooze(callback: CallbackQuery, *, booking_id: int, due_at: datetime) -> None:
    try:
        async with active_session() as session:
            repo = ScheduledNotificationRepository(session)
            master = await MasterRepository(session).get_by_telegram_id(int(callback.from_user.id))
            booking = await BookingRepository(session).get_for_review(int(booking_id))
            if booking.master.id != master.id:
                await callback.answer(common_txt.generic_error(), show_alert=False)
                return
            await repo.snooze_attendance_nudges_for_booking(booking_id=int(booking_id), due_at=due_at)
    except Exception as exc:
        await ev.aexception("attendance_reminder.snooze_failed", exc=exc, booking_id=int(booking_id))
        await callback.answer(common_txt.generic_error(), show_alert=True)
        return

    await callback.answer(common_txt.saved(), show_alert=False)
    await _hide_keyboard(callback)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith(f"{ATT_PREFIX}attended:"))
async def attendance_mark_attended(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_attendance_reminder", step="attended")
    booking_id = _parse_booking_id(callback.data or "", prefix=f"{ATT_PREFIX}attended:")
    if booking_id is None:
        await callback.answer(common_txt.generic_error(), show_alert=False)
        return
    await _mark_attendance(callback, booking_id=booking_id, outcome=AttendanceOutcome.ATTENDED)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith(f"{ATT_PREFIX}no_show:"))
async def attendance_mark_no_show(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_attendance_reminder", step="no_show")
    booking_id = _parse_booking_id(callback.data or "", prefix=f"{ATT_PREFIX}no_show:")
    if booking_id is None:
        await callback.answer(common_txt.generic_error(), show_alert=False)
        return
    await _mark_attendance(callback, booking_id=booking_id, outcome=AttendanceOutcome.NO_SHOW)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith(f"{ATT_PREFIX}snooze3h:"))
async def attendance_snooze_3h(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_attendance_reminder", step="snooze3h")
    booking_id = _parse_booking_id(callback.data or "", prefix=f"{ATT_PREFIX}snooze3h:")
    if booking_id is None:
        await callback.answer(common_txt.generic_error(), show_alert=False)
        return
    await _snooze(callback, booking_id=booking_id, due_at=datetime.now(UTC) + timedelta(hours=3))


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith(f"{ATT_PREFIX}tomorrow10:"))
async def attendance_snooze_tomorrow_10(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_attendance_reminder", step="tomorrow10")
    booking_id = _parse_booking_id(callback.data or "", prefix=f"{ATT_PREFIX}tomorrow10:")
    if booking_id is None:
        await callback.answer(common_txt.generic_error(), show_alert=False)
        return

    try:
        async with active_session() as session:
            master = await MasterRepository(session).get_by_telegram_id(int(callback.from_user.id))
        master_tz = get_timezone(str(master.timezone.value))
        local = datetime.now(master_tz)
        tomorrow = (local + timedelta(days=1)).date()
        due_local = datetime.combine(tomorrow, time_type(10, 0), tzinfo=master_tz)
        due_at = due_local.astimezone(UTC)
    except Exception as exc:
        await ev.aexception("attendance_reminder.tomorrow10_failed", exc=exc, booking_id=int(booking_id))
        await callback.answer(common_txt.generic_error(), show_alert=True)
        return

    await _snooze(callback, booking_id=booking_id, due_at=due_at)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith(f"{ATT_PREFIX}disable:"))
async def attendance_disable(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_attendance_reminder", step="disable")
    booking_id = _parse_booking_id(callback.data or "", prefix=f"{ATT_PREFIX}disable:")
    if booking_id is None:
        await callback.answer(common_txt.generic_error(), show_alert=False)
        return

    try:
        async with active_session() as session:
            await ScheduledNotificationRepository(session).cancel_attendance_nudges_for_booking(
                booking_id=int(booking_id),
            )
    except Exception as exc:
        await ev.aexception("attendance_reminder.disable_failed", exc=exc, booking_id=int(booking_id))
        await callback.answer(common_txt.generic_error(), show_alert=True)
        return

    await callback.answer(common_txt.saved(), show_alert=False)
    await _hide_keyboard(callback)
