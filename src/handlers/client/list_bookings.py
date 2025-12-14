import logging
from datetime import UTC, datetime
from textwrap import dedent

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.core.sa import active_session, session_local
from src.datetime_utils import to_zone
from src.handlers.client.messages import CLIENT_NOT_FOUND_MESSAGE
from src.repositories import ClientNotFound, ClientRepository
from src.repositories.booking import BookingNotFound, BookingRepository
from src.schemas import BookingForReview
from src.schemas.enums import BookingStatus, Timezone, status_badge, BOOKING_STATUS_MAP

logger = logging.getLogger(__name__)
router = Router(name=__name__)


def build_booking_cancel_keyboard(booking_id: int):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить запись", callback_data=f"c:booking:{booking_id}:cancel")],
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
        text = dedent(f"""
            <b>{booking.master.name}</b>
            
            {badge} {BOOKING_STATUS_MAP[booking.status]}
            📅 {slot_client:%d.%m.%Y}
            ⏰ {slot_client:%H:%M}
        """).strip()

        can_cancel = booking.start_at > datetime.now(UTC)
        reply_markup = build_booking_cancel_keyboard(booking.id) if can_cancel else None

        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)


@router.message(F.text == "📋 Мои записи")
async def client_list_bookings(message: Message, state: FSMContext) -> None:
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
            "Пока нет активных записей 🗓\n\n"
            "Чтобы записаться — нажми «➕ Записаться».",
        )
        return

    await message.answer("Твои активные записи 🗓")
    await _list_bookings(message.bot, telegram_id, bookings, client.timezone)
    await message.delete()


@router.callback_query(F.data.startswith("c:booking:"))
async def client_cancel_booking(callback: CallbackQuery, state: FSMContext):
    booking_id = _parse_booking_id(callback.data)
    if booking_id is None:
        await callback.answer("Некорректная команда.", show_alert=True)
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
                text="Запись не найдена или уже удалена.",
                show_alert=True,
            )
            return

        # безопасность: клиент может отменять только свою запись
        if booking.client.id != client.id:
            await callback.answer("Это не ваша запись.", show_alert=True)
            return

        cancelled = await booking_repo.cancel_by_client(
            booking_id=booking_id,
            client_id=client.id,
        )

    if not cancelled:
        await callback.answer(
            text="Не получилось отменить: запись уже обработана или время прошло.",
            show_alert=True,
        )
        return

    await callback.answer("Запись отменена ✅")

    if callback.message:
        await callback.message.edit_text("❌ Запись отменена.")

    # уведомляем мастера (в его TZ)
    slot_master = to_zone(booking.start_at, booking.master.timezone)
    slot_master_str = slot_master.strftime("%d.%m.%Y %H:%M")

    await callback.bot.send_message(
        chat_id=booking.master.telegram_id,
        text=(
            "❌ Запись отменена клиентом\n\n"
            f"<b>Клиент:</b> {booking.client.name}\n"
            f"<b>Дата/время:</b> {slot_master_str}"
        ),
    )
