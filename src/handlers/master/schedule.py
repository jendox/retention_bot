import logging
from calendar import monthrange
from datetime import UTC, date, datetime, time, timedelta
from textwrap import dedent
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import session_local
from src.datetime_utils import get_timezone
from src.filters.user_role import UserRole
from src.repositories import MasterRepository
from src.repositories.booking import BookingRepository
from src.schemas.enums import BookingStatus
from src.user_context import ActiveRole

logger = logging.getLogger(__name__)
router = Router(name=__name__)

SCHEDULE_CB = {
    "today": "m:schedule:today",
    "tomorrow": "m:schedule:tomorrow",
    "week": "m:schedule:week",
    "month": "m:schedule:month",
    "back": "m:schedule:back",
}


def _build_schedule_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 Сегодня", callback_data=SCHEDULE_CB["today"]),
                InlineKeyboardButton(text="📆 Завтра", callback_data=SCHEDULE_CB["tomorrow"]),
            ],
            [
                InlineKeyboardButton(text="📆 Неделя", callback_data=SCHEDULE_CB["week"]),
                InlineKeyboardButton(text="🗓 Месяц", callback_data=SCHEDULE_CB["month"]),
            ],
            [
                InlineKeyboardButton(text="◀️ Назад", callback_data=SCHEDULE_CB["back"]),
            ],
        ],
    )


def _status_badge(status: BookingStatus) -> str:
    badges = {
        BookingStatus.PENDING: "⏳",
        BookingStatus.CONFIRMED: "✅",
        BookingStatus.DECLINED: "❌",
        BookingStatus.CANCELLED: "🚫",
        BookingStatus.COMPLETED: "🟢",
    }
    return badges.get(status, "")


def _format_bookings(
    *,
    title: str,
    bookings,
    tz: ZoneInfo,
    cutoff_local: datetime | None = None,
) -> str:
    if cutoff_local:
        bookings = [
            booking for booking in bookings
            if booking.start_at.astimezone(tz) >= cutoff_local
        ]

    if not bookings:
        return f"{title}\n\nНет записей в выбранном периоде 🙂"

    lines: list[str] = [title, ""]
    for booking in bookings:
        local_dt = booking.start_at.astimezone(tz)
        badge = _status_badge(booking.status)
        client = getattr(booking, "client", None)
        client_name = getattr(client, "name", f"Клиент #{getattr(booking, 'client_id', '')}")
        phone = getattr(client, "phone", None)
        lines.append(
            f'{badge} {local_dt:%d.%m.%y %H:%M} <a href="tel:{phone}">📞 {client_name}</a>'
        )

    logger.debug(lines)
    return "\n".join(lines)


async def _fetch_master(telegram_id: int):
    async with session_local() as session:
        repo = MasterRepository(session)
        master = await repo.get_by_telegram_id(telegram_id)
    return master


def _same_day_next_month(current: date) -> date:
    next_month = current.month + 1
    next_year = current.year + (next_month - 1) // 12
    next_month = ((next_month - 1) % 12) + 1
    days_in_target = monthrange(next_year, next_month)[1]
    target_day = min(current.day, days_in_target)
    return date(next_year, next_month, target_day)


async def _send_schedule(callback: CallbackQuery, *, scope: str) -> None:
    master = await _fetch_master(callback.from_user.id)
    master_tz = get_timezone(str(master.timezone.value))

    now_local = datetime.now(master_tz)
    cutoff_local: datetime | None = now_local
    async with session_local() as session:
        repo = BookingRepository(session)

        if scope == "today":
            start_local = now_local
            end_local = datetime.combine(now_local.date() + timedelta(days=1), time(0, 0), tzinfo=master_tz)
        elif scope == "tomorrow":
            tomorrow = now_local.date() + timedelta(days=1)
            start_local = datetime.combine(tomorrow, time(0, 0), tzinfo=master_tz)
            end_local = start_local + timedelta(days=1)
            cutoff_local = None  # показываем весь завтрашний день
        elif scope == "week":
            start_local = now_local
            end_local = datetime.combine(now_local.date() + timedelta(days=8), time(0, 0), tzinfo=master_tz)
        else:
            start_local = now_local
            month_end_date = _same_day_next_month(now_local.date())
            end_local = datetime.combine(month_end_date, time(0, 0), tzinfo=master_tz) + timedelta(days=1)

        statuses = BookingStatus.active() if scope not in ["today", "tomorrow"] else BookingStatus.without_completed()
        bookings = await repo.get_for_master_in_range(
            master_id=master.id,
            start_at_utc=start_local.astimezone(UTC),
            end_at_utc=end_local.astimezone(UTC),
            statuses=statuses,
            load_clients=True,
        )

    title_map = {
        "today": "Расписание на сегодня",
        "tomorrow": "Расписание на завтра",
        "week": "Расписание на неделю",
        "month": "Расписание на месяц",
    }
    text = _format_bookings(
        title=title_map.get(scope, "Расписание"),
        bookings=bookings,
        tz=master_tz,
        cutoff_local=cutoff_local,
    )

    await callback.message.edit_text(
        text=text,
        reply_markup=_build_schedule_keyboard(),
        parse_mode="HTML",
    )


async def master_schedule(message: Message) -> None:
    await message.answer(
        text=dedent(
            """
            Расписание мастера
            Выбери период, чтобы посмотреть записи. Прошедшие записи за сегодня скрыты.
            """,
        ).strip(),
        reply_markup=_build_schedule_keyboard(),
    )
    try:
        await message.delete()
    except Exception:
        logger.debug("schedule.delete_source_failed", exc_info=True)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.in_(SCHEDULE_CB.values()))
async def master_schedule_callbacks(callback: CallbackQuery) -> None:
    data = callback.data or ""
    if data == SCHEDULE_CB["back"]:
        await callback.answer("Возвращаемся в главное меню.")
        try:
            await callback.message.delete()
        except Exception:
            logger.debug("schedule.delete_menu_failed", exc_info=True)
        return

    await callback.answer()
    if data == SCHEDULE_CB["today"]:
        await _send_schedule(callback, scope="today")
    elif data == SCHEDULE_CB["tomorrow"]:
        await _send_schedule(callback, scope="tomorrow")
    elif data == SCHEDULE_CB["week"]:
        await _send_schedule(callback, scope="week")
    elif data == SCHEDULE_CB["month"]:
        await _send_schedule(callback, scope="month")
