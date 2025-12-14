import logging
from calendar import monthrange
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from textwrap import dedent
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import active_session, session_local
from src.datetime_utils import get_timezone
from src.filters.user_role import UserRole
from src.repositories import MasterRepository
from src.repositories.booking import BookingRepository
from src.schemas.enums import BOOKING_STATUS_MAP, BookingStatus, status_badge
from src.user_context import ActiveRole

logger = logging.getLogger(__name__)
router = Router(name=__name__)


class Scope(StrEnum):
    TODAY = "today"
    TOMORROW = "tomorrow"
    WEEK = "week"
    MONTH = "month"

    @classmethod
    def short(cls) -> set["Scope"]:
        return {cls.TODAY, cls.TOMORROW}

    @classmethod
    def long(cls) -> set["Scope"]:
        return {cls.WEEK, cls.MONTH}


PER_PAGE = 10

SCHEDULE_CB: dict[str, str] = {
    Scope.TODAY.value: f"m:schedule:{Scope.TODAY.value}",
    Scope.TOMORROW.value: f"m:schedule:{Scope.TOMORROW.value}",
    Scope.WEEK.value: f"m:schedule:{Scope.WEEK.value}",
    Scope.MONTH.value: f"m:schedule:{Scope.MONTH.value}",
    "back_menu": "m:schedule:back",
    "back_periods": "m:schedule:periods",
}

TITLE_MAP: dict[Scope, str] = {
    Scope.TODAY: "Расписание на сегодня",
    Scope.TOMORROW: "Расписание на завтра",
    Scope.WEEK: "Расписание на неделю",
    Scope.MONTH: "Расписание на месяц",
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
                    text="📅 Сегодня", callback_data=SCHEDULE_CB[str(Scope.TODAY.value)],
                ),
                InlineKeyboardButton(
                    text="📆 Завтра", callback_data=SCHEDULE_CB[str(Scope.TOMORROW.value)],
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📆 Неделя", callback_data=SCHEDULE_CB[str(Scope.WEEK.value)],
                ),
                InlineKeyboardButton(
                    text="🗓 Месяц", callback_data=SCHEDULE_CB[str(Scope.MONTH.value)],
                ),
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад", callback_data=SCHEDULE_CB["back_menu"],
                ),
            ],
        ],
    )


def _button_text(booking, tz: ZoneInfo, scope: Scope) -> str:
    """Text shown on booking buttons."""
    local_dt = booking.start_at.astimezone(tz)
    badge = status_badge(booking.status)

    client = getattr(booking, "client", None)
    client_name = getattr(client, "name", None) or f"Клиент #{getattr(booking, 'client_id', '')}"

    if scope in Scope.long():
        return f"{badge} {local_dt:%d.%m} {local_dt:%H:%M} · {client_name}"
    return f"{badge} {local_dt:%H:%M} · {client_name}"


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

    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=SCHEDULE_CB["back_periods"])])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_booking_card_keyboard(
    *,
    booking_id: int,
    status: BookingStatus,
    scope: Scope,
    page: int,
) -> InlineKeyboardMarkup:
    inline_keyboard: list[list[InlineKeyboardButton]] = []
    if status in BookingStatus.active():
        inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=cb_action("cancel", booking_id, scope, page),
                ),
                InlineKeyboardButton(
                    text="🔄 Перенести",
                    callback_data=cb_action("reschedule", booking_id, scope, page),
                ),
            ],
        )
    inline_keyboard.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад к расписанию",
                callback_data=cb_schedule(scope, page),
            ),
        ],
    )
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


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
        start_local = now_local
        end_local = datetime.combine(
            now_local.date() + timedelta(days=1), time(0, 0), tzinfo=master_tz,
        )
        cutoff_local: datetime | None = now_local
        return start_local, end_local, cutoff_local

    if scope == Scope.TOMORROW:
        tomorrow = now_local.date() + timedelta(days=1)
        start_local = datetime.combine(tomorrow, time(0, 0), tzinfo=master_tz)
        end_local = start_local + timedelta(days=1)
        cutoff_local = None  # show whole tomorrow
        return start_local, end_local, cutoff_local

    if scope == Scope.WEEK:
        start_local = now_local
        end_local = datetime.combine(
            now_local.date() + timedelta(days=8), time(0, 0), tzinfo=master_tz,
        )
        cutoff_local = now_local
        return start_local, end_local, cutoff_local

    # Scope.MONTH
    start_local = now_local
    month_end_date = _same_day_next_month(now_local.date())
    end_local = datetime.combine(month_end_date, time(0, 0), tzinfo=master_tz) + timedelta(days=1)
    cutoff_local = now_local
    return start_local, end_local, cutoff_local


async def _cancel_booking(booking_id: int) -> bool:
    async with active_session() as session:
        repo = BookingRepository(session)
        return await repo.set_status(booking_id, BookingStatus.CANCELLED)


# ---------- rendering ----------

async def _send_schedule(callback: CallbackQuery, *, scope: Scope, page: int = 1) -> None:
    master = await _fetch_master(callback.from_user.id)
    master_tz = get_timezone(str(master.timezone.value))

    start_local, end_local, cutoff_local = _compute_range(master_tz, scope)

    statuses = BookingStatus.active() if scope in Scope.long() else BookingStatus.without_completed()
    async with session_local() as session:
        repo = BookingRepository(session)
        bookings = await repo.get_for_master_in_range(
            master_id=master.id,
            start_at_utc=start_local.astimezone(UTC),
            end_at_utc=end_local.astimezone(UTC),
            statuses=statuses,
            load_clients=True,
        )

    if cutoff_local:
        bookings = [
            booking for booking in bookings if booking.start_at.astimezone(master_tz) >= cutoff_local
        ]

    title = TITLE_MAP.get(scope, "Расписание")

    if not bookings:
        text = f"{title}\n\nЗдесь пока нет записей 🙂"
        reply_markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=SCHEDULE_CB["back_periods"])]],
        )
    else:
        text = f"{title}\nВыбери запись:"
        reply_markup = _build_bookings_list_keyboard(bookings=bookings, tz=master_tz, scope=scope, page=page)

    await callback.message.edit_text(text=text, reply_markup=reply_markup, parse_mode="HTML")


async def _send_booking_card(callback: CallbackQuery, *, booking_id: int, scope: Scope, page: int) -> None:
    master = await _fetch_master(callback.from_user.id)
    master_tz = get_timezone(str(master.timezone.value))

    async with session_local() as session:
        repo = BookingRepository(session)
        booking = await repo.get_for_review(booking_id)

    client = getattr(booking, "client", None)
    client_name = getattr(client, "name", None) or f"Клиент #{getattr(booking, 'client_id', '')}"
    phone = getattr(client, "phone", None)

    phone_line = f'<a href="tel:{phone}">{phone}</a>' if phone else "не указан"

    local_dt = booking.start_at.astimezone(master_tz)
    badge = status_badge(booking.status)

    text = dedent(f"""
        Запись

        {badge} {BOOKING_STATUS_MAP[booking.status]}
        📅 {local_dt:%d.%m.%Y}
        ⏰ {local_dt:%H:%M}

        👤 {client_name}
        📞 {phone_line}
        """).strip()

    await callback.message.edit_text(
        text=text,
        reply_markup=_build_booking_card_keyboard(booking_id=booking_id, scope=scope, page=page),
        parse_mode="HTML",
    )


# ---------- entrypoint ----------

async def master_schedule(message: Message) -> None:
    await message.answer(
        text="Выбери период, чтобы посмотреть записи:",
        reply_markup=_build_period_keyboard(),
    )


# ---------- callbacks ----------

@router.callback_query(UserRole(ActiveRole.MASTER), F.data == "m:noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.in_(SCHEDULE_CB.values()))
async def master_schedule_period_callbacks(callback: CallbackQuery) -> None:
    data = callback.data or ""

    if data == SCHEDULE_CB["back_menu"]:
        await callback.answer("Возвращаемся в главное меню.")
        try:
            await callback.message.delete()
        except Exception:
            logger.debug("schedule.delete_menu_failed", exc_info=True)
        return

    if data == SCHEDULE_CB["back_periods"]:
        await callback.answer()
        await callback.message.edit_text(
            text="Выбери период, чтобы посмотреть записи:",
            reply_markup=_build_period_keyboard(),
        )
        return

    await callback.answer()

    scope = Scope(data.split(":")[-1])
    await _send_schedule(callback, scope=scope, page=1)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith("m:s:"))
async def master_schedule_pagination(callback: CallbackQuery) -> None:
    # m:s:<scope>:p:<page>
    parts = (callback.data or "").split(":")
    # ["m","s","week","p","2"]
    try:
        scope = Scope(parts[2])
        page = int(parts[4])
    except Exception:
        await callback.answer("Ошибка навигации.", show_alert=False)
        logger.debug("schedule.pagination_parse_failed", extra={"data": callback.data}, exc_info=True)
        return

    await callback.answer()
    await _send_schedule(callback, scope=scope, page=page)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith("m:b:"))
async def master_open_booking_card(callback: CallbackQuery) -> None:
    # m:b:<booking_id>:s:<scope>:p:<page>
    parts = (callback.data or "").split(":")
    try:
        booking_id = int(parts[2])
        scope = Scope(parts[4])
        page = int(parts[6])
    except Exception:
        await callback.answer("Ошибка открытия записи.", show_alert=False)
        logger.debug("schedule.open_booking_parse_failed", extra={"data": callback.data}, exc_info=True)
        return

    await callback.answer()
    await _send_booking_card(callback, booking_id=booking_id, scope=scope, page=page)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith("m:a:"))
async def master_booking_actions(callback: CallbackQuery) -> None:
    # m:a:<action>:<booking_id>:s:<scope>:p:<page>
    parts = (callback.data or "").split(":")
    try:
        action = parts[2]
        booking_id = int(parts[3])
        scope = Scope(parts[5])
        page = int(parts[7])
    except Exception:
        await callback.answer("Ошибка действия.", show_alert=False)
        logger.debug("schedule.action_parse_failed", extra={"data": callback.data}, exc_info=True)
        return

    if action == "cancel":
        if not await _cancel_booking(booking_id):
            await callback.answer("Не удалось отменить запись.", show_alert=True)
            logger.error("schedule.cancel_failed", exc_info=True)
            return

        await callback.answer("Запись отменена ✅", show_alert=True)
        await _send_schedule(callback, scope=scope, page=page)
        return

    if action == "reschedule":
        # Placeholder: start your FSM/calendar/time-slot flow here.
        await callback.answer("Перенос: тут запускаем выбор новой даты/времени (TODO).", show_alert=True)
        return

    await callback.answer("Неизвестное действие.", show_alert=False)
