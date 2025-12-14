import logging
from datetime import UTC

from aiogram import F, Router
from aiogram.types import CallbackQuery

from src.core.sa import active_session
from src.datetime_utils import to_zone
from src.repositories import (
    BookingRepository,
)
from src.schemas.enums import BookingStatus

router = Router(name=__name__)
logger = logging.getLogger(__name__)


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
        await callback.answer("Некорректная команда.", show_alert=True)
        return

    booking_id, action = parsed
    master_telegram_id = callback.from_user.id

    async with active_session() as session:
        repo = BookingRepository(session)

        booking = await repo.get_for_review(booking_id)

        # Безопасность: мастер может подтверждать только свои записи
        if booking.master.telegram_id != master_telegram_id:
            await callback.answer("Это не твоя запись.", show_alert=True)
            return

        # Не даём обработать повторно
        if booking.status != BookingStatus.PENDING:
            await callback.answer("Эта запись уже обработана.", show_alert=True)
            return

        new_status = BookingStatus.CONFIRMED if action == "confirm" else BookingStatus.DECLINED
        await repo.set_status(booking_id, new_status)

    await callback.answer("Готово ✅")

    # Тексты (мастеру — в его TZ, клиенту — в его TZ)
    slot_master = to_zone(booking.start_at.astimezone(UTC), booking.master.timezone)
    slot_client = to_zone(booking.start_at.astimezone(UTC), booking.client.timezone)

    slot_master_str = slot_master.strftime("%d.%m.%Y %H:%M")
    slot_client_str = slot_client.strftime("%d.%m.%Y %H:%M")

    if new_status == BookingStatus.CONFIRMED:
        master_text = (
            "✅ Запись подтверждена.\n\n"
            f"<b>Клиент:</b> {booking.client.name}\n"
            f"<b>Дата/время:</b> {slot_master_str}\n"
        )
        client_text = (
            "✅ Запись подтверждена мастером.\n\n"
            f"<b>Дата/время:</b> {slot_client_str}\n"
            "Ждём вас 🙂"
        )
    else:
        master_text = (
            "❌ Запись отклонена.\n\n"
            f"<b>Клиент:</b> {booking.client.name}\n"
            f"<b>Дата/время:</b> {slot_master_str}\n"
        )
        client_text = (
            "❌ Мастер отклонил запись.\n\n"
            f"<b>Дата/время:</b> {slot_client_str}\n"
            "Пожалуйста, выбери другое время в разделе «➕ Записаться»."
        )

    # Обновляем сообщение мастеру (убираем кнопки)
    if callback.message:
        await callback.message.edit_text(master_text)

    # Уведомляем клиента
    await callback.bot.send_message(
        chat_id=booking.client.telegram_id,
        text=client_text,
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
