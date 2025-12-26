from __future__ import annotations

from datetime import UTC, datetime
from html import escape as html_escape
from textwrap import dedent

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from src.core.sa import active_session, session_local
from src.datetime_utils import to_zone
from src.filters.user_role import UserRole
from src.handlers.shared.guards import rate_limit_callback, rate_limit_message
from src.handlers.shared.ui import safe_bot_delete_message, safe_delete, safe_edit_text
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
from src.texts.buttons import btn_back, btn_cancel_booking
from src.texts.client_messages import CLIENT_NOT_FOUND_MESSAGE
from src.texts.master_schedule import btn_cancel_no, btn_cancel_yes
from src.user_context import ActiveRole

router = Router(name=__name__)
ev = EventLogger(__name__)

LIST_BOOKINGS_MAIN_KEY = "client_list_bookings_main"
LIST_BOOKINGS_PAGE_KEY = "client_list_bookings_page"
PAGE_SIZE = 8


def _main_ref(chat_id: int, message_id: int) -> dict[str, int]:
    return {"chat_id": int(chat_id), "message_id": int(message_id)}


def _get_total_pages(total: int) -> int:
    if total <= 0:
        return 1
    return (total + PAGE_SIZE - 1) // PAGE_SIZE


def _clamp_page(page: int, total_pages: int) -> int:
    return max(1, min(int(page), int(total_pages)))


async def _get_main_message_id(state: FSMContext) -> tuple[int, int] | None:
    data = await state.get_data()
    ref = data.get(LIST_BOOKINGS_MAIN_KEY)
    if not isinstance(ref, dict):
        return None
    chat_id = ref.get("chat_id")
    message_id = ref.get("message_id")
    if not isinstance(chat_id, int) or not isinstance(message_id, int):
        return None
    return chat_id, message_id


async def _set_main_message(state: FSMContext, *, chat_id: int, message_id: int, page: int) -> None:
    await state.update_data(
        **{
            LIST_BOOKINGS_MAIN_KEY: _main_ref(chat_id, message_id),
            LIST_BOOKINGS_PAGE_KEY: int(page),
        },
    )


async def _clear_main_message(state: FSMContext) -> None:
    data = await state.get_data()
    if LIST_BOOKINGS_MAIN_KEY in data or LIST_BOOKINGS_PAGE_KEY in data:
        data.pop(LIST_BOOKINGS_MAIN_KEY, None)
        data.pop(LIST_BOOKINGS_PAGE_KEY, None)
        await state.set_data(data)


def _parse_int_suffix(prefix: str, data: str) -> int | None:
    if not data.startswith(prefix):
        return None
    try:
        return int(data[len(prefix) :])
    except ValueError:
        return None


def _parse_booking_id(data: str) -> int | None:
    parts = (data or "").split(":")

    # legacy: c:booking:<id>:cancel
    if len(parts) == 4 and parts[:2] == ["c", "booking"] and parts[3] == "cancel":  # noqa: PLR2004
        try:
            return int(parts[2])
        except ValueError:
            return None

    # new: c:bookings:<action>:<id>
    if len(parts) == 4 and parts[:2] == ["c", "bookings"]:  # noqa: PLR2004
        if parts[2] in {"open", "cancel", "cancel_yes"}:
            try:
                return int(parts[3])
            except ValueError:
                return None

    return None


def _parse_bookings_action(data: str) -> tuple[str, int | None] | None:
    parts = (data or "").split(":")
    if len(parts) < 3 or parts[0] != "c" or parts[1] != "bookings":  # noqa: PLR2004
        return None

    action = parts[2]
    value: int | None = None
    if action in {"close", "back"}:
        return action, None

    if len(parts) != 4:  # noqa: PLR2004
        return None

    try:
        value = int(parts[3])
    except ValueError:
        return None

    if action in {"page", "open", "cancel", "cancel_yes"}:
        return action, value
    return None


def _build_booking_row_text(
    *,
    index: int,
    booking: BookingForReview,
    client_timezone: Timezone,
) -> str:
    slot_client = to_zone(booking.start_at, client_timezone)
    badge = status_badge(booking.status)
    status_label = BOOKING_STATUS_MAP[booking.status]
    master_name_safe = html_escape(str(getattr(booking.master, "name", "")))
    return f"{index}. {badge} {slot_client:%d.%m %H:%M} — <b>{master_name_safe}</b> ({status_label})"


def _build_bookings_page_text(
    *,
    bookings: list[BookingForReview],
    page: int,
    total_pages: int,
    client_timezone: Timezone,
) -> str:
    start_index = (page - 1) * PAGE_SIZE
    lines = [txt.title_page(page=page, total_pages=total_pages), ""]
    for offset, booking in enumerate(bookings):
        lines.append(
            _build_booking_row_text(
                index=start_index + offset + 1,
                booking=booking,
                client_timezone=client_timezone,
            ),
        )
    return "\n".join(lines).strip()


def _build_page_keyboard(
    *,
    bookings: list[BookingForReview],
    page: int,
    total_pages: int,
    client_timezone: Timezone,
) -> InlineKeyboardMarkup:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows: list[list[InlineKeyboardButton]] = []
    for booking in bookings:
        slot_client = to_zone(booking.start_at, client_timezone)
        master_name = str(getattr(booking.master, "name", ""))
        label = f"{slot_client:%d.%m %H:%M} • {master_name}".strip()
        rows.append([InlineKeyboardButton(text=label, callback_data=f"c:bookings:open:{booking.id}")])

    nav: list[InlineKeyboardButton] = []
    if total_pages > 1 and page > 1:
        nav.append(InlineKeyboardButton(text=txt.btn_prev(), callback_data=f"c:bookings:page:{page - 1}"))
    if total_pages > 1 and page < total_pages:
        nav.append(InlineKeyboardButton(text=txt.btn_next(), callback_data=f"c:bookings:page:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text=txt.btn_close(), callback_data="c:bookings:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_details_keyboard(*, booking_id: int, can_cancel: bool) -> InlineKeyboardMarkup:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows: list[list[InlineKeyboardButton]] = []
    if can_cancel:
        rows.append([InlineKeyboardButton(text=btn_cancel_booking(), callback_data=f"c:bookings:cancel:{booking_id}")])
    rows.append([InlineKeyboardButton(text=btn_back(), callback_data="c:bookings:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_cancel_confirm_keyboard(*, booking_id: int) -> InlineKeyboardMarkup:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=btn_cancel_yes(), callback_data=f"c:bookings:cancel_yes:{booking_id}"),
                InlineKeyboardButton(text=btn_cancel_no(), callback_data=f"c:bookings:open:{booking_id}"),
            ],
        ],
    )


async def _fetch_client_bookings(
    telegram_id: int,
) -> tuple[Timezone, list[BookingForReview]] | None:
    async with session_local() as session:
        client_repo = ClientRepository(session)
        booking_repo = BookingRepository(session)

        try:
            client = await client_repo.get_by_telegram_id(telegram_id)
        except ClientNotFound:
            return None

        bookings = await booking_repo.get_for_client(
            client_id=client.id,
            statuses=BookingStatus.active(),
            limit=30,
        )
        return client.timezone, bookings


async def start_client_list_bookings(
    message: Message,
    state: FSMContext,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="client_list_bookings", step="start")
    if not await rate_limit_message(message, rate_limiter, name="client_list_bookings:start", ttl_sec=2):
        return
    telegram_id = message.from_user.id

    fetched = await _fetch_client_bookings(telegram_id)
    if fetched is None:
        await message.answer(CLIENT_NOT_FOUND_MESSAGE)
        return

    client_timezone, all_bookings = fetched
    if not all_bookings:
        await message.answer(txt.empty_list())
        await safe_delete(message, ev=ev, event="client_list_bookings.delete_menu_message_failed")
        return

    previous = await _get_main_message_id(state)
    if previous is not None:
        prev_chat_id, prev_message_id = previous
        await safe_bot_delete_message(
            message.bot,
            chat_id=prev_chat_id,
            message_id=prev_message_id,
            ev=ev,
            event="client_list_bookings.delete_previous_failed",
        )
        await _clear_main_message(state)

    total_pages = _get_total_pages(len(all_bookings))
    page = 1
    page_bookings = all_bookings[:PAGE_SIZE]
    sent = await message.answer(
        _build_bookings_page_text(
            bookings=page_bookings,
            page=page,
            total_pages=total_pages,
            client_timezone=client_timezone,
        ),
        reply_markup=_build_page_keyboard(
            bookings=page_bookings,
            page=page,
            total_pages=total_pages,
            client_timezone=client_timezone,
        ),
        parse_mode="HTML",
    )
    await _set_main_message(state, chat_id=sent.chat.id, message_id=sent.message_id, page=page)
    await safe_delete(message, ev=ev, event="client_list_bookings.delete_menu_message_failed")


async def _render_list(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    page: int,
) -> None:
    fetched = await _fetch_client_bookings(callback.from_user.id)
    if fetched is None:
        await callback.answer(CLIENT_NOT_FOUND_MESSAGE, show_alert=True)
        return
    client_timezone, all_bookings = fetched
    if not all_bookings:
        await callback.answer()
        if callback.message is not None:
            await safe_edit_text(
                callback.message,
                text=txt.empty_list(),
                reply_markup=None,
                parse_mode="HTML",
                ev=ev,
                event="client_list_bookings.edit_empty_failed",
            )
        await _clear_main_message(state)
        return

    await callback.answer()
    total_pages = _get_total_pages(len(all_bookings))
    page = _clamp_page(page, total_pages)
    start = (page - 1) * PAGE_SIZE
    page_bookings = all_bookings[start : start + PAGE_SIZE]

    if callback.message is not None:
        await safe_edit_text(
            callback.message,
            text=_build_bookings_page_text(
                bookings=page_bookings,
                page=page,
                total_pages=total_pages,
                client_timezone=client_timezone,
            ),
            reply_markup=_build_page_keyboard(
                bookings=page_bookings,
                page=page,
                total_pages=total_pages,
                client_timezone=client_timezone,
            ),
            parse_mode="HTML",
            ev=ev,
            event="client_list_bookings.edit_list_failed",
        )
        await _set_main_message(
            state,
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            page=page,
        )


async def _render_details(
    *,
    callback: CallbackQuery,
    booking_id: int,
    answer_on_success: bool = True,
) -> BookingForReview | None:
    async with session_local() as session:
        client_repo = ClientRepository(session)
        booking_repo = BookingRepository(session)
        try:
            client = await client_repo.get_by_telegram_id(callback.from_user.id)
        except ClientNotFound:
            await callback.answer(CLIENT_NOT_FOUND_MESSAGE, show_alert=True)
            return None
        try:
            booking = await booking_repo.get_for_review(booking_id)
        except BookingNotFound:
            await callback.answer(txt.booking_not_found(), show_alert=True)
            return None

        if booking.client.id != client.id:
            await callback.answer(txt.forbidden(), show_alert=True)
            return None

    slot_client = to_zone(booking.start_at, client.timezone)
    badge = status_badge(booking.status)
    master_name_safe = html_escape(str(getattr(booking.master, "name", "")))
    status_label = BOOKING_STATUS_MAP[booking.status]

    text = dedent(f"""
        <b>{txt.details_title()}</b>\n
        <b>{master_name_safe}</b>
        {badge} {status_label}
        📅 {slot_client:%d.%m.%Y}
        ⏰ {slot_client:%H:%M}
    """).strip()

    can_cancel = booking.start_at > datetime.now(UTC)
    if callback.message is not None:
        if answer_on_success:
            await callback.answer()
        await safe_edit_text(
            callback.message,
            text=text,
            reply_markup=_build_details_keyboard(booking_id=booking_id, can_cancel=can_cancel),
            parse_mode="HTML",
            ev=ev,
            event="client_list_bookings.edit_details_failed",
        )
    return booking


async def _handle_close(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    value: int | None,
) -> None:
    del notifier, value
    await callback.answer()
    if callback.message is not None:
        deleted = await safe_delete(callback.message, ev=ev, event="client_list_bookings.close_delete_failed")
        if not deleted:
            await callback.message.edit_reply_markup(reply_markup=None)
    await _clear_main_message(state)


async def _handle_back(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    value: int | None,
) -> None:
    del notifier, value
    page = int((await state.get_data()).get(LIST_BOOKINGS_PAGE_KEY, 1))
    await _render_list(callback=callback, state=state, page=page)


async def _handle_page(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    value: int | None,
) -> None:
    del notifier
    if value is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return
    await _render_list(callback=callback, state=state, page=value)


async def _handle_open(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    value: int | None,
) -> None:
    del state, notifier
    if value is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return
    await _render_details(callback=callback, booking_id=value, answer_on_success=True)


async def _handle_cancel_prompt(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    value: int | None,
) -> None:
    del state, notifier
    if value is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return
    await callback.answer()
    if callback.message is None:
        return
    await safe_edit_text(
        callback.message,
        text=txt.cancel_confirm(),
        reply_markup=_build_cancel_confirm_keyboard(booking_id=value),
        parse_mode="HTML",
        ev=ev,
        event="client_list_bookings.edit_cancel_confirm_failed",
    )


async def _handle_cancel_yes(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    value: int | None,
) -> None:
    del state
    if value is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return
    booking = await _cancel_booking_and_notify(callback=callback, notifier=notifier, booking_id=value)
    if booking is None:
        return
    await _render_details(callback=callback, booking_id=value, answer_on_success=False)


@router.callback_query(UserRole(ActiveRole.CLIENT), F.data.startswith("c:bookings:"))
async def client_bookings_callbacks(
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="client_list_bookings", step="callbacks")
    if not await rate_limit_callback(callback, rate_limiter, name="client_list_bookings:callbacks", ttl_sec=1):
        return

    action = _parse_bookings_action(callback.data or "")
    if action is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return

    name, value = action
    handlers = {
        "close": _handle_close,
        "back": _handle_back,
        "page": _handle_page,
        "open": _handle_open,
        "cancel": _handle_cancel_prompt,
        "cancel_yes": _handle_cancel_yes,
    }
    handler = handlers.get(name)
    if handler is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return
    await handler(callback=callback, state=state, notifier=notifier, value=value)


async def _cancel_booking_and_notify(
    *,
    callback: CallbackQuery,
    notifier: Notifier,
    booking_id: int,
) -> BookingForReview | None:
    async with active_session() as session:
        client_repo = ClientRepository(session)
        booking_repo = BookingRepository(session)
        try:
            client = await client_repo.get_by_telegram_id(callback.from_user.id)
        except ClientNotFound:
            await callback.answer(CLIENT_NOT_FOUND_MESSAGE, show_alert=True)
            return None
        try:
            booking = await booking_repo.get_for_review(booking_id)
        except BookingNotFound:
            await callback.answer(txt.booking_not_found(), show_alert=True)
            return None

        if booking.client.id != client.id:
            await callback.answer(txt.forbidden(), show_alert=True)
            return None

        cancelled = await booking_repo.cancel_by_client(booking_id=booking_id, client_id=client.id)

    if not cancelled:
        await callback.answer(txt.cannot_cancel(), show_alert=True)
        return None

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

    await callback.answer(txt.cancelled_alert())
    return booking


@router.callback_query(UserRole(ActiveRole.CLIENT), F.data.startswith("c:booking:"))
async def client_cancel_booking_legacy(
    callback: CallbackQuery,
    notifier: Notifier,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="client_list_bookings", step="cancel_legacy")
    if not await rate_limit_callback(callback, rate_limiter, name="client_list_bookings:cancel_legacy", ttl_sec=2):
        return
    booking_id = _parse_booking_id(callback.data or "")
    if booking_id is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return

    await callback.answer()
    booking = await _cancel_booking_and_notify(callback=callback, notifier=notifier, booking_id=booking_id)
    if booking is None:
        return
    if callback.message is not None:
        await safe_edit_text(
            callback.message,
            text=txt.cancelled_text(),
            parse_mode="HTML",
            ev=ev,
            event="client_list_bookings.edit_cancelled_failed",
        )
