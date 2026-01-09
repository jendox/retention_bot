from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from html import escape as html_escape
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import active_session, session_local
from src.datetime_utils import get_timezone
from src.filters.user_role import UserRole
from src.handlers.shared.guards import rate_limit_callback
from src.handlers.shared.ui import safe_delete, safe_edit_text
from src.notifications import NotificationEvent
from src.notifications.notifier import Notifier
from src.notifications.outbox import BookingClientOutboxNotification, maybe_enqueue_booking_client_notification
from src.notifications.policy import NotificationPolicy
from src.observability.alerts import AdminAlerter
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.paywall import build_upgrade_button_with_fallback
from src.rate_limiter import RateLimiter
from src.repositories import MasterClientRepository, MasterRepository
from src.repositories.booking import BookingRepository
from src.repositories.scheduled_notification import ScheduledNotificationRepository
from src.schemas.enums import BOOKING_STATUS_MAP, AttendanceOutcome, BookingStatus, status_badge
from src.settings import get_settings
from src.texts import master_schedule as txt, paywall as paywall_txt
from src.texts.buttons import btn_back, btn_cancel_booking, btn_close, btn_confirm, btn_decline, btn_go_pro
from src.use_cases.entitlements import EntitlementsService
from src.user_context import ActiveRole

ev = EventLogger(__name__)
router = Router(name=__name__)


class Scope(StrEnum):
    TODAY = "today"
    TOMORROW = "tomorrow"
    WEEK = "week"
    MONTH = "month"
    YESTERDAY = "yesterday"
    HISTORY_WEEK = "history_week"

    @classmethod
    def short(cls) -> set["Scope"]:
        return {cls.TODAY, cls.TOMORROW}

    @classmethod
    def long(cls) -> set["Scope"]:
        return {cls.WEEK, cls.MONTH}

    @classmethod
    def history(cls) -> set["Scope"]:
        return {cls.YESTERDAY, cls.HISTORY_WEEK}


async def _apply_client_aliases_for_master(*, session, master_id: int, bookings: list) -> None:
    aliases = await MasterClientRepository(session).get_client_aliases_for_master(master_id=int(master_id))
    if not aliases:
        return
    for booking in bookings:
        client = getattr(booking, "client", None)
        client_id = getattr(client, "id", None)
        if client is None or client_id is None:
            continue
        alias = aliases.get(int(client_id))
        if alias:
            client.name = alias


async def _apply_client_alias_for_booking(*, session, master_id: int, booking) -> None:
    client = getattr(booking, "client", None)
    client_id = getattr(client, "id", None)
    if client is None or client_id is None:
        return
    alias = await MasterClientRepository(session).get_client_alias(master_id=int(master_id), client_id=int(client_id))
    if alias:
        client.name = alias


async def _fetch_master_or_none(callback: CallbackQuery):
    try:
        return await _fetch_master(callback.from_user.id)
    except Exception as exc:
        await ev.aexception("master_schedule.load_master_failed", exc=exc)
        return None


def _statuses_for_scope(scope: Scope):
    if scope in Scope.history():
        return BookingStatus.without_completed()
    return BookingStatus.active() if scope in Scope.long() else BookingStatus.without_completed()


async def _fetch_bookings_or_none(
    *,
    master_id: int,
    scope: Scope,
    start_at_utc: datetime,
    end_at_utc: datetime,
) -> list | None:
    try:
        async with session_local() as session:
            repo = BookingRepository(session)
            bookings = await repo.get_for_master_in_range(
                master_id=master_id,
                start_at_utc=start_at_utc,
                end_at_utc=end_at_utc,
                statuses=_statuses_for_scope(scope),
                load_clients=True,
            )
            await _apply_client_aliases_for_master(session=session, master_id=int(master_id), bookings=bookings)
            return bookings
    except Exception as exc:
        await ev.aexception(
            "master_schedule.load_bookings_failed",
            exc=exc,
            master_id=master_id,
            scope=scope.value,
        )
        return None


PER_PAGE = 10

SCHEDULE_CB: dict[str, str] = {
    Scope.TODAY.value: f"m:schedule:{Scope.TODAY.value}",
    Scope.TOMORROW.value: f"m:schedule:{Scope.TOMORROW.value}",
    Scope.WEEK.value: f"m:schedule:{Scope.WEEK.value}",
    Scope.MONTH.value: f"m:schedule:{Scope.MONTH.value}",
    Scope.YESTERDAY.value: f"m:schedule:{Scope.YESTERDAY.value}",
    Scope.HISTORY_WEEK.value: f"m:schedule:{Scope.HISTORY_WEEK.value}",
    "back_menu": "m:schedule:back",
    "back_periods": "m:schedule:periods",
}

TITLE_MAP: dict[Scope, str] = {
    Scope.TODAY: txt.title_today(),
    Scope.TOMORROW: txt.title_tomorrow(),
    Scope.WEEK: txt.title_week(),
    Scope.MONTH: txt.title_month(),
    Scope.YESTERDAY: txt.title_yesterday(),
    Scope.HISTORY_WEEK: txt.title_history_week(),
}


# ---------- callback builders (short + stable) ----------


def cb_schedule(scope: Scope, page: int) -> str:
    # m:s:<scope>:p:<page>
    return f"m:s:{scope.value}:p:{page}"


def cb_open_booking(booking_id: int, scope: Scope, page: int) -> str:
    # m:b:<booking_id>:s:<scope>:p:<page>
    return f"m:b:{booking_id}:s:{scope.value}:p:{page}"


def cb_action(action: str, booking_id: int, scope: Scope, page: int) -> str:
    # m:a:<action>:<booking_id>:s:<scope>:p:<page>
    return f"m:a:{action}:{booking_id}:s:{scope.value}:p:{page}"


# ---------- keyboards ----------


def _build_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=txt.btn_today(),
                    callback_data=SCHEDULE_CB[str(Scope.TODAY.value)],
                ),
                InlineKeyboardButton(
                    text=txt.btn_tomorrow(),
                    callback_data=SCHEDULE_CB[str(Scope.TOMORROW.value)],
                ),
            ],
            [
                InlineKeyboardButton(
                    text=txt.btn_week(),
                    callback_data=SCHEDULE_CB[str(Scope.WEEK.value)],
                ),
                InlineKeyboardButton(
                    text=txt.btn_month(),
                    callback_data=SCHEDULE_CB[str(Scope.MONTH.value)],
                ),
            ],
            [
                InlineKeyboardButton(
                    text=txt.btn_yesterday(),
                    callback_data=SCHEDULE_CB[str(Scope.YESTERDAY.value)],
                ),
                InlineKeyboardButton(
                    text=txt.btn_history_week(),
                    callback_data=SCHEDULE_CB[str(Scope.HISTORY_WEEK.value)],
                ),
            ],
            [
                InlineKeyboardButton(
                    text=txt.btn_override_day(),
                    callback_data="m:overrides:start",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=btn_close(),
                    callback_data=SCHEDULE_CB["back_menu"],
                ),
            ],
        ],
    )


def _button_text(booking, tz: ZoneInfo, scope: Scope) -> str:
    """Text shown on booking buttons."""
    local_dt = booking.start_at.astimezone(tz)
    badge = status_badge(booking.status)
    if scope in Scope.history() and booking.status == BookingStatus.CONFIRMED:
        attendance = getattr(booking, "attendance_outcome", AttendanceOutcome.UNKNOWN)
        badge = {
            AttendanceOutcome.ATTENDED: "✅",
            AttendanceOutcome.NO_SHOW: "🔴",
            AttendanceOutcome.UNKNOWN: "🕒",
        }.get(attendance, "🕒")

    client = getattr(booking, "client", None)
    client_name = getattr(client, "name", None) or txt.client_fallback(client_id=getattr(booking, "client_id", ""))
    client_name_safe = html_escape(str(client_name))

    if scope in Scope.long():
        return f"{badge} {local_dt:%d.%m} {local_dt:%H:%M} · {client_name_safe}"
    return f"{badge} {local_dt:%H:%M} · {client_name_safe}"


def _build_bookings_list_keyboard(
    *,
    bookings: list,
    tz: ZoneInfo,
    scope: Scope,
    page: int,
    per_page: int = PER_PAGE,
) -> InlineKeyboardMarkup:
    total = len(bookings)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    start = (page - 1) * per_page
    end = start + per_page
    page_items = bookings[start:end]

    rows: list[list[InlineKeyboardButton]] = []
    for booking in page_items:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_button_text(booking, tz, scope),
                    callback_data=cb_open_booking(booking.id, scope, page),
                ),
            ],
        )

    if total_pages > 1:
        nav_row: list[InlineKeyboardButton] = []
        if page > 1:
            nav_row.append(InlineKeyboardButton(text="◀️", callback_data=cb_schedule(scope, page - 1)))
        nav_row.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="m:noop"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton(text="▶️", callback_data=cb_schedule(scope, page + 1)))
        rows.append(nav_row)

    rows.append([InlineKeyboardButton(text=btn_back(), callback_data=SCHEDULE_CB["back_periods"])])
    rows.append([InlineKeyboardButton(text=btn_close(), callback_data=SCHEDULE_CB["back_menu"])])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_booking_card_keyboard(
    *,
    booking_id: int,
    scope: Scope,
    page: int,
    meta: "_BookingCardKeyboardMeta",
) -> InlineKeyboardMarkup:
    inline_keyboard: list[list[InlineKeyboardButton]] = []
    if meta.show_no_show_paywall and not meta.plan_is_pro:
        inline_keyboard.append(
            [
                build_upgrade_button_with_fallback(
                    contact=get_settings().billing.contact,
                    text=btn_go_pro(),
                    callback_data="billing:pro:start",
                    force_callback=True,
                ),
            ],
        )
    if meta.show_review_actions:
        inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text=btn_confirm(),
                    callback_data=f"m:booking:{int(booking_id)}:confirm",
                ),
                InlineKeyboardButton(
                    text=btn_decline(),
                    callback_data=f"m:booking:{int(booking_id)}:decline",
                ),
            ],
        )
    if meta.show_manage_actions:
        actions: list[InlineKeyboardButton] = [
            InlineKeyboardButton(
                text=btn_cancel_booking(),
                callback_data=cb_action("cancel", booking_id, scope, page),
            ),
        ]
        actions.append(
            InlineKeyboardButton(
                text=txt.btn_reschedule(),
                callback_data=cb_action("reschedule", booking_id, scope, page),
            ),
        )
        inline_keyboard.append(actions)
    if meta.show_attendance_actions and meta.attendance_outcome == AttendanceOutcome.UNKNOWN:
        inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text=txt.btn_mark_attended(),
                    callback_data=cb_action("attended", booking_id, scope, page),
                ),
                InlineKeyboardButton(
                    text=txt.btn_mark_no_show(),
                    callback_data=cb_action("no_show", booking_id, scope, page),
                ),
            ],
        )
    inline_keyboard.append(
        [
            InlineKeyboardButton(
                text=txt.btn_back_to_schedule(),
                callback_data=cb_schedule(scope, page),
            ),
        ],
    )
    inline_keyboard.append(
        [
            InlineKeyboardButton(
                text=btn_close(),
                callback_data=SCHEDULE_CB["back_menu"],
            ),
        ],
    )
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def _build_cancel_confirm_keyboard(*, booking_id: int, scope: Scope, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=txt.btn_cancel_yes(),
                    callback_data=cb_action("cancel_yes", booking_id, scope, page),
                ),
                InlineKeyboardButton(
                    text=txt.btn_cancel_no(),
                    callback_data=cb_action("cancel_no", booking_id, scope, page),
                ),
            ],
        ],
    )


# ---------- helpers ----------


async def _fetch_master(telegram_id: int):
    async with session_local() as session:
        repo = MasterRepository(session)
        return await repo.get_by_telegram_id(telegram_id)


def _same_day_next_month(current: date) -> date:
    next_month = current.month + 1
    next_year = current.year + (next_month - 1) // 12
    next_month = ((next_month - 1) % 12) + 1
    days_in_target = monthrange(next_year, next_month)[1]
    target_day = min(current.day, days_in_target)
    return date(next_year, next_month, target_day)


def _compute_range(master_tz: ZoneInfo, scope: Scope) -> tuple[datetime, datetime, datetime | None]:
    """
    Returns: (start_local, end_local, cutoff_local)
    cutoff_local used to hide past bookings "from now" for some scopes.
    """
    now_local = datetime.now(master_tz)

    if scope == Scope.TODAY:
        start_local = datetime.combine(now_local.date(), time(0, 0), tzinfo=master_tz)
        end_local = datetime.combine(
            now_local.date() + timedelta(days=1),
            time(0, 0),
            tzinfo=master_tz,
        )
        cutoff_local = None  # show whole day to allow attendance marking for past bookings
        return start_local, end_local, cutoff_local

    if scope == Scope.TOMORROW:
        tomorrow = now_local.date() + timedelta(days=1)
        start_local = datetime.combine(tomorrow, time(0, 0), tzinfo=master_tz)
        end_local = start_local + timedelta(days=1)
        cutoff_local = None  # show whole tomorrow
        return start_local, end_local, cutoff_local

    if scope == Scope.YESTERDAY:
        today = now_local.date()
        yesterday = today - timedelta(days=1)
        start_local = datetime.combine(yesterday, time(0, 0), tzinfo=master_tz)
        end_local = datetime.combine(today, time(0, 0), tzinfo=master_tz)
        cutoff_local = None
        return start_local, end_local, cutoff_local

    if scope == Scope.HISTORY_WEEK:
        today = now_local.date()
        start_local = datetime.combine(today - timedelta(days=7), time(0, 0), tzinfo=master_tz)
        end_local = datetime.combine(today, time(0, 0), tzinfo=master_tz)
        cutoff_local = None
        return start_local, end_local, cutoff_local

    if scope == Scope.WEEK:
        start_local = now_local
        end_local = datetime.combine(
            now_local.date() + timedelta(days=8),
            time(0, 0),
            tzinfo=master_tz,
        )
        cutoff_local = now_local
        return start_local, end_local, cutoff_local

    # Scope.MONTH
    start_local = now_local
    month_end_date = _same_day_next_month(now_local.date())
    end_local = datetime.combine(month_end_date, time(0, 0), tzinfo=master_tz) + timedelta(days=1)
    cutoff_local = now_local
    return start_local, end_local, cutoff_local


def _can_mark_attendance(*, status: BookingStatus, start_at: datetime, duration_min: int) -> bool:
    if status != BookingStatus.CONFIRMED:
        return False
    end_at_utc = start_at.astimezone(UTC) + timedelta(minutes=int(duration_min))
    return end_at_utc <= datetime.now(UTC)


@dataclass(frozen=True)
class _BookingCardKeyboardMeta:
    status: BookingStatus
    attendance_outcome: AttendanceOutcome
    show_attendance_actions: bool
    plan_is_pro: bool
    show_no_show_paywall: bool
    show_manage_actions: bool
    show_review_actions: bool


@dataclass(frozen=True)
class _BookingCardRender:
    text: str
    status: BookingStatus
    attendance_outcome: AttendanceOutcome
    show_attendance_actions: bool


def _render_booking_card(booking, *, master_tz: ZoneInfo) -> _BookingCardRender:
    client = getattr(booking, "client", None)
    client_name = getattr(client, "name", None) or txt.client_fallback(client_id=getattr(booking, "client_id", ""))
    client_name_safe = html_escape(str(client_name))

    phone = getattr(client, "phone", None)
    phone_safe = html_escape(str(phone)) if phone else None
    phone_line = f'<a href="tel:{phone_safe}">{phone_safe}</a>' if phone_safe else txt.phone_missing()

    local_dt = booking.start_at.astimezone(master_tz)
    badge = status_badge(booking.status)
    attendance = getattr(booking, "attendance_outcome", AttendanceOutcome.UNKNOWN)
    show_attendance_actions = _can_mark_attendance(
        status=booking.status,
        start_at=booking.start_at,
        duration_min=booking.duration_min,
    )

    text = txt.card(
        lines=[
            f"{badge} {BOOKING_STATUS_MAP[booking.status]}",
            f"📅 {local_dt:%d.%m.%Y}",
            f"⏰ {local_dt:%H:%M}",
            "",
            f"👤 {client_name_safe}",
            f"📞 {phone_line}",
            txt.attendance_line(outcome=attendance),
        ],
    )
    return _BookingCardRender(
        text=text,
        status=booking.status,
        attendance_outcome=attendance,
        show_attendance_actions=show_attendance_actions,
    )


async def _cancel_booking(*, booking_id: int, master_id: int) -> bool:
    async with active_session() as session:
        repo = BookingRepository(session)
        return await repo.cancel_by_master(booking_id=booking_id, master_id=master_id)


async def _send_or_edit(
    callback: CallbackQuery,
    *,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    if callback.message is not None:
        await safe_edit_text(
            callback.message,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            ev=ev,
            event="master_schedule.edit_failed",
        )
        return
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=text,
        reply_markup=reply_markup,
        parse_mode="HTML",
    )


async def _maybe_notify_client_cancelled(
    *,
    booking,
    plan_is_pro: bool,
    policy: NotificationPolicy,
) -> None:
    client_tg = getattr(booking.client, "telegram_id", None)
    if client_tg is None:
        return

    async with active_session() as session:
        await maybe_enqueue_booking_client_notification(
            policy=policy,
            outbox=ScheduledNotificationRepository(session),
            request=BookingClientOutboxNotification(
                event=NotificationEvent.BOOKING_CANCELLED_BY_MASTER,
                chat_id=int(client_tg),
                booking_id=int(booking.id),
                booking_start_at=booking.start_at,
                now_utc=datetime.now(UTC),
                plan_is_pro=bool(plan_is_pro),
                master_notify_clients=bool(getattr(booking.master, "notify_clients", True)),
                client_notifications_enabled=bool(getattr(booking.client, "notifications_enabled", True)),
            ),
        )


# ---------- rendering ----------


async def _send_schedule(callback: CallbackQuery, *, scope: Scope, page: int = 1) -> None:
    bind_log_context(flow="master_schedule", step="send_schedule")
    master = await _fetch_master_or_none(callback)
    if master is None:
        await callback.answer(txt.navigation_error(), show_alert=True)
        return
    master_tz = get_timezone(str(master.timezone.value))

    start_local, end_local, cutoff_local = _compute_range(master_tz, scope)

    bookings = await _fetch_bookings_or_none(
        master_id=int(master.id),
        scope=scope,
        start_at_utc=start_local.astimezone(UTC),
        end_at_utc=end_local.astimezone(UTC),
    )
    if bookings is None:
        await callback.answer(txt.navigation_error(), show_alert=True)
        return

    if cutoff_local:
        bookings = [booking for booking in bookings if booking.start_at.astimezone(master_tz) >= cutoff_local]

    title = TITLE_MAP.get(scope, txt.title_default())

    if not bookings:
        text = txt.empty(title=title)
        reply_markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=btn_back(), callback_data=SCHEDULE_CB["back_periods"])]],
        )
    else:
        text = txt.choose_booking(title=title)
        reply_markup = _build_bookings_list_keyboard(bookings=bookings, tz=master_tz, scope=scope, page=page)

    await _send_or_edit(callback, text=text, reply_markup=reply_markup)


async def _send_booking_card(callback: CallbackQuery, *, booking_id: int, scope: Scope, page: int) -> None:
    bind_log_context(flow="master_schedule", step="booking_card")
    try:
        master = await _fetch_master(callback.from_user.id)
    except Exception as exc:
        await ev.aexception("master_schedule.load_master_failed", exc=exc)
        await callback.answer(txt.open_booking_error(), show_alert=True)
        return
    master_tz = get_timezone(str(master.timezone.value))

    try:
        async with session_local() as session:
            repo = BookingRepository(session)
            booking = await repo.get_for_review(booking_id)
            entitlements = EntitlementsService(session)
            plan = await entitlements.get_plan(master_id=master.id)
            await _apply_client_alias_for_booking(session=session, master_id=int(master.id), booking=booking)
    except Exception as exc:
        await ev.aexception("master_schedule.load_booking_failed", exc=exc, booking_id=booking_id)
        await callback.answer(txt.open_booking_error(), show_alert=True)
        return

    if booking.master.id != master.id:
        await callback.answer(txt.no_access(), show_alert=True)
        await _send_schedule(callback, scope=scope, page=page)
        return

    render = _render_booking_card(booking, master_tz=master_tz)
    text = render.text
    show_no_show_paywall = (not plan.is_pro) and (render.attendance_outcome == AttendanceOutcome.NO_SHOW)
    if show_no_show_paywall:
        text = f"{text}\n\n{paywall_txt.no_show_value()}"

    show_manage_actions = (
        scope not in Scope.history()
        and booking.status == BookingStatus.CONFIRMED
        and booking.start_at.astimezone(UTC) > datetime.now(UTC)
    )
    show_review_actions = (
        scope not in Scope.history()
        and booking.status == BookingStatus.PENDING
        and booking.start_at.astimezone(UTC) > datetime.now(UTC)
    )
    await _send_or_edit(
        callback,
        text=text,
        reply_markup=_build_booking_card_keyboard(
            booking_id=booking_id,
            scope=scope,
            page=page,
            meta=_BookingCardKeyboardMeta(
                status=render.status,
                attendance_outcome=render.attendance_outcome,
                show_attendance_actions=render.show_attendance_actions,
                plan_is_pro=bool(plan.is_pro),
                show_no_show_paywall=bool(show_no_show_paywall),
                show_manage_actions=bool(show_manage_actions),
                show_review_actions=bool(show_review_actions),
            ),
        ),
    )


async def _send_cancel_confirm_card(callback: CallbackQuery, *, booking_id: int, scope: Scope, page: int) -> None:
    bind_log_context(flow="master_schedule", step="cancel_confirm")
    try:
        master = await _fetch_master(callback.from_user.id)
    except Exception as exc:
        await ev.aexception("master_schedule.load_master_failed", exc=exc)
        await callback.answer(txt.action_error(), show_alert=True)
        return

    master_tz = get_timezone(str(master.timezone.value))
    try:
        async with session_local() as session:
            repo = BookingRepository(session)
            booking = await repo.get_for_review(booking_id)
            await _apply_client_alias_for_booking(session=session, master_id=int(master.id), booking=booking)
    except Exception as exc:
        await ev.aexception("master_schedule.load_booking_failed", exc=exc, booking_id=booking_id)
        await callback.answer(txt.action_error(), show_alert=True)
        return

    if booking.master.id != master.id:
        await callback.answer(txt.no_access(), show_alert=True)
        await _send_schedule(callback, scope=scope, page=page)
        return

    client = getattr(booking, "client", None)
    client_name = getattr(client, "name", None) or txt.client_fallback(client_id=getattr(booking, "client_id", ""))
    client_name_safe = html_escape(str(client_name))
    phone = getattr(client, "phone", None)
    phone_safe = html_escape(str(phone)) if phone else None
    phone_line = f'<a href="tel:{phone_safe}">{phone_safe}</a>' if phone_safe else txt.phone_missing()

    local_dt = booking.start_at.astimezone(master_tz)
    badge = status_badge(booking.status)
    attendance = getattr(booking, "attendance_outcome", AttendanceOutcome.UNKNOWN)
    text = txt.card(
        lines=[
            f"{badge} {BOOKING_STATUS_MAP[booking.status]}",
            f"📅 {local_dt:%d.%m.%Y}",
            f"⏰ {local_dt:%H:%M}",
            "",
            f"👤 {client_name_safe}",
            f"📞 {phone_line}",
            txt.attendance_line(outcome=attendance),
        ],
    )
    text = f"{text}\n\n{txt.cancel_confirm_prompt()}"

    await _send_or_edit(
        callback,
        text=text,
        reply_markup=_build_cancel_confirm_keyboard(booking_id=booking_id, scope=scope, page=page),
    )


async def _handle_action_cancel_prompt(callback: CallbackQuery, *, booking_id: int, scope: Scope, page: int) -> None:
    await callback.answer()
    await _send_cancel_confirm_card(callback, booking_id=booking_id, scope=scope, page=page)


async def _handle_action_cancel_no(callback: CallbackQuery, *, booking_id: int, scope: Scope, page: int) -> None:
    await callback.answer()
    await _send_booking_card(callback, booking_id=booking_id, scope=scope, page=page)


async def _handle_action_cancel_yes(
    callback: CallbackQuery,
    *,
    booking_id: int,
    scope: Scope,
    page: int,
    notifier: Notifier,
    admin_alerter: AdminAlerter | None,
) -> None:
    ev.info(
        "booking.cancel_attempt",
        actor="master",
        booking_id=int(booking_id),
        master_telegram_id=int(callback.from_user.id),
    )
    try:
        master = await _fetch_master(callback.from_user.id)
        ok = await _cancel_booking(booking_id=booking_id, master_id=master.id)
    except Exception as exc:
        ev.info(
            "booking.cancel_rejected",
            actor="master",
            booking_id=int(booking_id),
            master_telegram_id=int(callback.from_user.id),
            error="exception",
        )
        await ev.aexception(
            "master_schedule.cancel_failed",
            exc=exc,
            admin_alerter=admin_alerter,
            booking_id=booking_id,
        )
        await callback.answer(txt.cancel_failed(), show_alert=True)
        return

    if not ok:
        ev.info(
            "booking.cancel_rejected",
            actor="master",
            booking_id=int(booking_id),
            master_id=int(master.id),
            error="cannot_cancel",
        )
        await callback.answer(txt.cancel_failed(), show_alert=True)
        return

    ev.info(
        "booking.cancelled",
        actor="master",
        booking_id=int(booking_id),
        master_id=int(master.id),
    )
    await callback.answer(txt.cancelled_ok(), show_alert=True)

    # Notify client (Pro-only + toggles checked by policy).
    try:
        async with session_local() as session:
            booking_repo = BookingRepository(session)
            booking = await booking_repo.get_for_review(booking_id)
            entitlements = EntitlementsService(session)
            plan = await entitlements.get_plan(master_id=master.id)
        await _maybe_notify_client_cancelled(booking=booking, plan_is_pro=plan.is_pro, policy=notifier.policy)
    except Exception as exc:
        await ev.aexception(
            "master_schedule.cancel_notify_failed",
            exc=exc,
            booking_id=booking_id,
            admin_alerter=admin_alerter,
        )

    await _send_schedule(callback, scope=scope, page=page)


async def _handle_action_reschedule(
    callback: CallbackQuery,
    *,
    booking_id: int,
    scope: Scope,
    page: int,
    state: FSMContext,
    rate_limiter: RateLimiter | None,
) -> None:
    from src.handlers.master.reschedule import start_reschedule

    try:
        async with session_local() as session:
            master = await MasterRepository(session).get_by_telegram_id(callback.from_user.id)
            plan = await EntitlementsService(session).get_plan(master_id=master.id)
    except Exception as exc:
        await ev.aexception("master_schedule.reschedule_plan_check_failed", exc=exc, booking_id=booking_id)
        await callback.answer(txt.action_error(), show_alert=True)
        return

    if not plan.is_pro:
        await callback.answer()
        if callback.message is not None:
            back_to_card = f"m:b:{booking_id}:s:{scope.value}:p:{page}"
            await safe_edit_text(
                callback.message,
                text=paywall_txt.reschedule_pro_only(),
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                build_upgrade_button_with_fallback(
                                    contact=get_settings().billing.contact,
                                    text=btn_go_pro(),
                                    callback_data="billing:pro:start",
                                    force_callback=True,
                                ),
                            ],
                            [InlineKeyboardButton(text=btn_back(), callback_data=back_to_card)],
                        ],
                    ),
                    parse_mode="HTML",
                    ev=ev,
                    event="master_schedule.paywall_reschedule_edit_failed",
                )
        return

    await start_reschedule(callback, state, rate_limiter, booking_id=booking_id, scope=scope, page=page)


async def _handle_action_attendance(
    callback: CallbackQuery,
    *,
    booking_id: int,
    scope: Scope,
    page: int,
    outcome: AttendanceOutcome,
    admin_alerter: AdminAlerter | None,
) -> None:
    from src.use_cases.mark_booking_attendance import MarkBookingAttendance, MarkBookingAttendanceRequest

    try:
        async with active_session() as session:
            use_case = MarkBookingAttendance(session)
            result = await use_case.execute(
                MarkBookingAttendanceRequest(
                    master_telegram_id=callback.from_user.id,
                    booking_id=booking_id,
                    outcome=outcome,
                ),
            )
    except Exception as exc:
        await ev.aexception(
            "master_schedule.attendance_failed",
            exc=exc,
            booking_id=booking_id,
            outcome=str(outcome),
            admin_alerter=admin_alerter,
        )
        await callback.answer(txt.attendance_failed(), show_alert=True)
        return

    if result.ok:
        ev.info("master_schedule.attendance_marked", booking_id=booking_id, outcome=str(outcome))
        await callback.answer(txt.attendance_marked(), show_alert=False)
        await _send_booking_card(callback, booking_id=booking_id, scope=scope, page=page)
        return

    if result.error and result.error.value == "already_marked":
        await callback.answer(txt.attendance_already_marked(), show_alert=False)
        await _send_booking_card(callback, booking_id=booking_id, scope=scope, page=page)
        return

    await callback.answer(txt.attendance_not_eligible(), show_alert=True)
    await _send_schedule(callback, scope=scope, page=page)


# ---------- entrypoint ----------


async def master_schedule(message: Message) -> None:
    bind_log_context(flow="master_schedule", step="start")
    await message.answer(
        text=txt.choose_period(),
        reply_markup=_build_period_keyboard(),
    )


# ---------- callbacks ----------


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == "m:noop")
async def noop(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_schedule", step="noop")
    await callback.answer()


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.in_(SCHEDULE_CB.values()))
async def master_schedule_period_callbacks(callback: CallbackQuery, rate_limiter: RateLimiter | None = None) -> None:
    bind_log_context(flow="master_schedule", step="choose_period")
    if not await rate_limit_callback(callback, rate_limiter, name="master_schedule:navigate", ttl_sec=1):
        return
    data = callback.data or ""

    if data == SCHEDULE_CB["back_menu"]:
        await callback.answer()
        if callback.message is not None:
            await safe_delete(callback.message, ev=ev, event="schedule.delete_menu_failed")
        return

    if data == SCHEDULE_CB["back_periods"]:
        await callback.answer()
        await _send_or_edit(callback, text=txt.choose_period(), reply_markup=_build_period_keyboard())
        return

    await callback.answer()

    try:
        scope = Scope(data.split(":")[-1])
    except Exception:
        await callback.answer(txt.navigation_error(), show_alert=True)
        return
    await _send_schedule(callback, scope=scope, page=1)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith("m:s:"))
async def master_schedule_pagination(callback: CallbackQuery, rate_limiter: RateLimiter | None = None) -> None:
    bind_log_context(flow="master_schedule", step="pagination")
    # m:s:<scope>:p:<page>
    parts = (callback.data or "").split(":")
    # ["m","s","week","p","2"]
    try:
        scope = Scope(parts[2])
        page = int(parts[4])
    except Exception:
        await callback.answer(txt.navigation_error(), show_alert=False)
        ev.debug("schedule.pagination_parse_failed")
        return

    if not await rate_limit_callback(
        callback,
        rate_limiter,
        name="master_schedule:navigate",
        ttl_sec=1,
        scope=str(getattr(scope, "value", scope)),
        page=page,
    ):
        return
    await callback.answer()
    await _send_schedule(callback, scope=scope, page=page)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith("m:b:"))
async def master_open_booking_card(callback: CallbackQuery, rate_limiter: RateLimiter | None = None) -> None:
    bind_log_context(flow="master_schedule", step="open_booking")
    # m:b:<booking_id>:s:<scope>:p:<page>
    parts = (callback.data or "").split(":")
    try:
        booking_id = int(parts[2])
        scope = Scope(parts[4])
        page = int(parts[6])
    except Exception:
        await callback.answer(txt.open_booking_error(), show_alert=False)
        ev.debug("schedule.open_booking_parse_failed")
        return
    if not await rate_limit_callback(
        callback,
        rate_limiter,
        name="master_schedule:open_booking",
        ttl_sec=1,
        booking_id=booking_id,
    ):
        return
    await callback.answer()
    await _send_booking_card(callback, booking_id=booking_id, scope=scope, page=page)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith("m:a:"))
async def master_booking_actions(
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    rate_limiter: RateLimiter | None = None,
    admin_alerter: AdminAlerter | None = None,
) -> None:
    bind_log_context(flow="master_schedule", step="action")
    # m:a:<action>:<booking_id>:s:<scope>:p:<page>
    parts = (callback.data or "").split(":")
    try:
        action = parts[2]
        booking_id = int(parts[3])
        scope = Scope(parts[5])
        page = int(parts[7])
    except Exception:
        await callback.answer(txt.action_error(), show_alert=False)
        ev.debug("schedule.action_parse_failed")
        return

    if not await rate_limit_callback(
        callback,
        rate_limiter,
        name="master_schedule:action",
        ttl_sec=2,
        action=action,
        booking_id=booking_id,
    ):
        return

    handlers = {
        "cancel": lambda: _handle_action_cancel_prompt(callback, booking_id=booking_id, scope=scope, page=page),
        "cancel_no": lambda: _handle_action_cancel_no(callback, booking_id=booking_id, scope=scope, page=page),
        "cancel_yes": lambda: _handle_action_cancel_yes(
            callback,
            booking_id=booking_id,
            scope=scope,
            page=page,
            notifier=notifier,
            admin_alerter=admin_alerter,
        ),
        "reschedule": lambda: _handle_action_reschedule(
            callback,
            booking_id=booking_id,
            scope=scope,
            page=page,
            state=state,
            rate_limiter=rate_limiter,
        ),
        "attended": lambda: _handle_action_attendance(
            callback,
            booking_id=booking_id,
            scope=scope,
            page=page,
            outcome=AttendanceOutcome.ATTENDED,
            admin_alerter=admin_alerter,
        ),
        "no_show": lambda: _handle_action_attendance(
            callback,
            booking_id=booking_id,
            scope=scope,
            page=page,
            outcome=AttendanceOutcome.NO_SHOW,
            admin_alerter=admin_alerter,
        ),
    }
    handler = handlers.get(action)
    if handler is None:
        await callback.answer(txt.unknown_action(), show_alert=False)
        return
    await handler()
