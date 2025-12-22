import logging
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from src.core.sa import active_session
from src.datetime_utils import to_zone
from src.notifications import BookingContext, NotificationEvent, NotificationService, RecipientKind
from src.repositories import (
    BookingRepository,
)
from src.schemas.enums import BookingStatus
from src.texts import master_booking_review as txt
from src.texts.buttons import btn_cancel_booking
from src.use_cases.entitlements import EntitlementsService

router = Router(name=__name__)
logger = logging.getLogger(__name__)


def _build_client_cancel_keyboard(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=btn_cancel_booking(), callback_data=f"c:booking:{booking_id}:cancel")],
        ],
    )


def _parse_review_callback(data: str) -> tuple[int, str] | None:
    # m:booking:{booking_id}:confirm|decline
    parts = data.split(":")
    if len(parts) != 4:  # noqa: PLR2004
        return None
    if parts[0] != "m" or parts[1] != "booking":
        return None
    try:
        booking_id = int(parts[2])
    except ValueError:
        return None
    action = parts[3]
    if action not in {"confirm", "decline"}:
        return None
    return booking_id, action


@router.callback_query(F.data.startswith("m:booking:"))
async def master_review_booking(callback: CallbackQuery) -> None:
    parsed = _parse_review_callback(callback.data or "")
    if parsed is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return

    booking_id, action = parsed
    master_telegram_id = callback.from_user.id

    async with active_session() as session:
        repo = BookingRepository(session)
        entitlements = EntitlementsService(session)

        booking = await repo.get_for_review(booking_id)

        # Безопасность: мастер может подтверждать только свои записи
        if booking.master.telegram_id != master_telegram_id:
            await callback.answer(txt.not_your_booking(), show_alert=True)
            return

        new_status = BookingStatus.CONFIRMED if action == "confirm" else BookingStatus.DECLINED
        changed = await repo.set_status_if_pending_for_master(
            booking_id=booking_id,
            master_id=booking.master.id,
            status=new_status,
        )
        if not changed:
            await callback.answer(txt.already_handled(), show_alert=True)
            return

        plan = await entitlements.get_plan(master_id=booking.master.id)
        allow_client_notifications = (
            plan.is_pro
            and bool(getattr(booking.master, "notify_clients", True))
            and bool(getattr(booking.client, "notifications_enabled", True))
        )

    await callback.answer(txt.done())

    # Тексты (мастеру — в его TZ, клиенту — в его TZ)
    slot_master = to_zone(booking.start_at.astimezone(UTC), booking.master.timezone)
    slot_client = to_zone(booking.start_at.astimezone(UTC), booking.client.timezone)

    slot_master_str = slot_master.strftime("%d.%m.%Y %H:%M")
    slot_client_str = slot_client.strftime("%d.%m.%Y %H:%M")

    if new_status == BookingStatus.CONFIRMED:
        master_text = txt.master_confirmed(client_name=booking.client.name, slot_str=slot_master_str)
        client_text = txt.client_confirmed(slot_str=slot_client_str)
    else:
        master_text = txt.master_declined(client_name=booking.client.name, slot_str=slot_master_str)
        client_text = txt.client_declined(slot_str=slot_client_str)

    # Обновляем сообщение мастеру (убираем кнопки)
    if callback.message:
        await callback.message.edit_text(master_text)

    if allow_client_notifications:
        reply_markup = None
        if new_status == BookingStatus.CONFIRMED and booking.start_at > datetime.now(UTC):
            reply_markup = _build_client_cancel_keyboard(booking.id)
        notification = NotificationService(callback.bot)
        await notification.send_booking(
            event=NotificationEvent.BOOKING_CONFIRMED if new_status == BookingStatus.CONFIRMED else NotificationEvent.BOOKING_DECLINED,
            recipient=RecipientKind.CLIENT,
            chat_id=booking.client.telegram_id,
            context=BookingContext(
                booking_id=booking.id,
                master_name=booking.master.name,
                client_name=booking.client.name,
                slot_str=slot_client_str,
                duration_min=booking.duration_min,
            ),
            reply_markup=reply_markup,
        )

    logger.info(
        "booking.reviewed",
        extra={
            "booking_id": booking.id,
            "new_status": new_status,
            "master_id": booking.master.id,
            "client_id": booking.client.id,
        },
    )
