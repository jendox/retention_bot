from datetime import UTC, datetime
from html import escape as html_escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from src.core.sa import active_session
from src.datetime_utils import to_zone
from src.handlers.master.guards import rate_limit_callback
from src.handlers.master.ui import safe_edit_reply_markup, safe_edit_text
from src.notifications import BookingContext, NotificationEvent, RecipientKind
from src.notifications.notifier import NotificationRequest, Notifier
from src.notifications.policy import NotificationFacts
from src.observability.alerts import AdminAlerter
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.rate_limiter import RateLimiter
from src.schemas.enums import BookingStatus
from src.texts import master_booking_review as txt
from src.texts.buttons import btn_cancel_booking
from src.use_cases.review_master_booking import (
    ReviewMasterBooking,
    ReviewMasterBookingAction,
    ReviewMasterBookingError,
    ReviewMasterBookingRequest,
)

router = Router(name=__name__)
ev = EventLogger(__name__)


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


def _review_error_text(error: ReviewMasterBookingError | None) -> str:
    if error == ReviewMasterBookingError.FORBIDDEN:
        return txt.not_your_booking()
    if error == ReviewMasterBookingError.ALREADY_HANDLED:
        return txt.already_handled()
    if error == ReviewMasterBookingError.PAST_BOOKING:
        return txt.past_booking()
    return txt.failed()


async def _disable_keyboard(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    await safe_edit_reply_markup(
        callback.message,
        reply_markup=None,
        ev=ev,
        event="master_booking_review.disable_keyboard_failed",
    )


def _master_review_text(*, booking, new_status: BookingStatus) -> str:
    slot_master = to_zone(booking.start_at.astimezone(UTC), booking.master.timezone)
    slot_master_str = slot_master.strftime("%d.%m.%Y %H:%M")
    client_name_safe = html_escape(str(booking.client.name))
    if new_status == BookingStatus.CONFIRMED:
        return txt.master_confirmed(client_name=client_name_safe, slot_str=slot_master_str)
    return txt.master_declined(client_name=client_name_safe, slot_str=slot_master_str)


async def _maybe_notify_client(
    *,
    notifier: Notifier,
    booking,
    new_status: BookingStatus,
    plan_is_pro: bool | None,
) -> None:
    slot_client = to_zone(booking.start_at.astimezone(UTC), booking.client.timezone)
    slot_client_str = slot_client.strftime("%d.%m.%Y %H:%M")

    event = (
        NotificationEvent.BOOKING_CONFIRMED
        if new_status == BookingStatus.CONFIRMED
        else NotificationEvent.BOOKING_DECLINED
    )
    reply_markup = None
    if new_status == BookingStatus.CONFIRMED and booking.start_at > datetime.now(UTC):
        reply_markup = _build_client_cancel_keyboard(booking.id)

    await notifier.maybe_send(
        NotificationRequest(
            event=event,
            recipient=RecipientKind.CLIENT,
            chat_id=booking.client.telegram_id,
            context=BookingContext(
                booking_id=booking.id,
                master_name=html_escape(str(booking.master.name)),
                client_name=html_escape(str(booking.client.name)),
                slot_str=slot_client_str,
                duration_min=booking.duration_min,
            ),
            facts=NotificationFacts(
                event=event,
                recipient=RecipientKind.CLIENT,
                chat_id=booking.client.telegram_id,
                plan_is_pro=bool(plan_is_pro),
                master_notify_clients=bool(getattr(booking.master, "notify_clients", True)),
                client_notifications_enabled=bool(getattr(booking.client, "notifications_enabled", True)),
                booking_start_at_utc=booking.start_at.astimezone(UTC),
            ),
            reply_markup=reply_markup,
        ),
    )


@router.callback_query(F.data.startswith("m:booking:"))
async def master_review_booking(
    callback: CallbackQuery,
    notifier: Notifier,
    rate_limiter: RateLimiter | None = None,
    admin_alerter: AdminAlerter | None = None,
) -> None:
    parsed = _parse_review_callback(callback.data or "")
    if parsed is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return

    booking_id, action = parsed
    if not await rate_limit_callback(
        callback,
        rate_limiter,
        name="master_booking_review:action",
        ttl_sec=2,
        booking_id=booking_id,
        action=action,
    ):
        return
    master_telegram_id = callback.from_user.id
    bind_log_context(flow="master_booking_review", step=action)

    await _disable_keyboard(callback)

    try:
        async with active_session() as session:
            result = await ReviewMasterBooking(session).execute(
                ReviewMasterBookingRequest(
                    master_telegram_id=master_telegram_id,
                    booking_id=booking_id,
                    action=ReviewMasterBookingAction(action),
                ),
            )
    except Exception as exc:
        await ev.aexception(
            "master_booking_review.failed",
            exc=exc,
            admin_alerter=admin_alerter,
            booking_id=booking_id,
            action=action,
        )
        await callback.answer(txt.failed(), show_alert=True)
        return

    ev.info(
        "master_booking_review.result",
        ok=bool(result.ok),
        error=str(result.error.value) if result.error else None,
        booking_id=booking_id,
        action=action,
    )

    if not result.ok:
        await callback.answer(_review_error_text(result.error), show_alert=True)
        return

    booking = result.booking
    new_status = result.new_status
    if booking is None or new_status is None:
        await callback.answer(txt.failed(), show_alert=True)
        return

    master_text = _master_review_text(booking=booking, new_status=new_status)

    if callback.message:
        await safe_edit_text(
            callback.message,
            text=master_text,
            parse_mode="HTML",
            ev=ev,
            event="master_booking_review.edit_failed",
        )
    await callback.answer(txt.done(), show_alert=False)

    await _maybe_notify_client(
        notifier=notifier,
        booking=booking,
        new_status=new_status,
        plan_is_pro=result.plan_is_pro,
    )

    ev.info(
        "booking.reviewed",
        action=action,
        booking_id=booking.id,
        new_status=new_status,
        master_id=booking.master.id,
        client_id=booking.client.id,
    )
