from __future__ import annotations

from datetime import UTC, datetime
from html import escape as html_escape
from textwrap import dedent

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.core.sa import active_session, session_local
from src.datetime_utils import to_zone
from src.filters.user_role import UserRole
from src.handlers.shared.guards import rate_limit_callback, rate_limit_message
from src.handlers.shared.ui import safe_delete, safe_edit_text
from src.notifications import BookingContext, NotificationEvent, RecipientKind
from src.notifications.notifier import NotificationRequest, Notifier
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.rate_limiter import RateLimiter
from src.repositories import ClientNotFound, ClientRepository
from src.repositories.booking import BookingNotFound, BookingRepository
from src.schemas import BookingForReview
from src.schemas.enums import BOOKING_STATUS_MAP, BookingStatus, Timezone, status_badge
from src.texts import client_list_bookings as txt
from src.texts.buttons import btn_cancel_booking
from src.texts.client_messages import CLIENT_NOT_FOUND_MESSAGE
from src.user_context import ActiveRole

router = Router(name=__name__)
ev = EventLogger(__name__)


def build_booking_cancel_keyboard(booking_id: int):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=btn_cancel_booking(), callback_data=f"c:booking:{booking_id}:cancel")],
        ],
    )


def _parse_booking_id(command: str) -> int | None:
    parts = (command or "").split(":")

    if len(parts) != 4 or parts[0] != "c" or parts[1] != "booking" or parts[3] != "cancel":  # noqa: PLR2004
        return None

    try:
        return int(parts[2])
    except ValueError:
        return None


async def _list_bookings(
    bot: Bot,
    chat_id: int,
    bookings: list[BookingForReview],
    client_timezone: Timezone,
) -> None:
    for booking in bookings:
        slot_client = to_zone(booking.start_at, client_timezone)
        badge = status_badge(booking.status)
        master_name_safe = html_escape(str(getattr(booking.master, "name", "")))
        text = dedent(f"""
            <b>{master_name_safe}</b>\n
            {badge} {BOOKING_STATUS_MAP[booking.status]}
            📅 {slot_client:%d.%m.%Y}
            ⏰ {slot_client:%H:%M}
        """).strip()

        can_cancel = booking.start_at > datetime.now(UTC)
        reply_markup = build_booking_cancel_keyboard(booking.id) if can_cancel else None

        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode="HTML")


async def start_client_list_bookings(
    message: Message,
    state: FSMContext,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="client_list_bookings", step="start")
    if not await rate_limit_message(message, rate_limiter, name="client_list_bookings:start", ttl_sec=2):
        return
    telegram_id = message.from_user.id

    async with session_local() as session:
        client_repo = ClientRepository(session)
        booking_repo = BookingRepository(session)

        try:
            client = await client_repo.get_by_telegram_id(telegram_id)
        except ClientNotFound:
            await message.answer(CLIENT_NOT_FOUND_MESSAGE)
            return

        bookings = await booking_repo.get_for_client(
            client_id=client.id,
            statuses=BookingStatus.active(),
            limit=30,
        )

    if not bookings:
        await message.answer(
            txt.empty_list(),
        )
        return

    await message.answer(txt.title())
    await _list_bookings(message.bot, telegram_id, bookings, client.timezone)
    await safe_delete(message, ev=ev, event="client_list_bookings.delete_menu_message_failed")


@router.callback_query(UserRole(ActiveRole.CLIENT), F.data.startswith("c:booking:"))
async def client_cancel_booking(
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="client_list_bookings", step="cancel")
    if not await rate_limit_callback(callback, rate_limiter, name="client_list_bookings:cancel", ttl_sec=2):
        return
    booking_id = _parse_booking_id(callback.data)
    if booking_id is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return

    telegram_id = callback.from_user.id

    async with active_session() as session:
        client_repo = ClientRepository(session)
        booking_repo = BookingRepository(session)

        client = await client_repo.get_by_telegram_id(telegram_id)
        try:
            booking = await booking_repo.get_for_review(booking_id)
        except BookingNotFound:
            await callback.answer(
                text=txt.booking_not_found(),
                show_alert=True,
            )
            return

        # безопасность: клиент может отменять только свою запись
        if booking.client.id != client.id:
            await callback.answer(txt.forbidden(), show_alert=True)
            return

        cancelled = await booking_repo.cancel_by_client(
            booking_id=booking_id,
            client_id=client.id,
        )

    if not cancelled:
        await callback.answer(
            text=txt.cannot_cancel(),
            show_alert=True,
        )
        return

    await callback.answer(txt.cancelled_alert())

    if callback.message is not None:
        await safe_edit_text(
            callback.message,
            text=txt.cancelled_text(),
            parse_mode="HTML",
            ev=ev,
            event="client_list_bookings.edit_cancelled_failed",
        )

    # уведомляем мастера (в его TZ)
    slot_master = to_zone(booking.start_at, booking.master.timezone)
    slot_master_str = slot_master.strftime("%d.%m.%Y %H:%M")

    await notifier.maybe_send(
        NotificationRequest(
            event=NotificationEvent.BOOKING_CANCELLED_BY_CLIENT,
            recipient=RecipientKind.MASTER,
            chat_id=booking.master.telegram_id,
            context=BookingContext(
                booking_id=booking.id,
                master_name=html_escape(str(getattr(booking.master, "name", ""))),
                client_name=html_escape(str(getattr(booking.client, "name", ""))),
                slot_str=slot_master_str,
                duration_min=booking.duration_min,
            ),
        ),
    )
