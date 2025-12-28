from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape as html_escape
from textwrap import dedent

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

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
from src.texts.buttons import btn_back, btn_cancel_booking, btn_close
from src.texts.client_messages import CLIENT_NOT_FOUND_MESSAGE
from src.texts.master_schedule import btn_cancel_no, btn_cancel_yes
from src.user_context import ActiveRole

router = Router(name=__name__)
ev = EventLogger(__name__)

CB_PREFIX = "c:bookings:"
LIST_BOOKINGS_MAIN_KEY = "client_list_bookings_main"
LIST_BOOKINGS_PAGE_KEY = "client_list_bookings_page"

TEXT_PAGE_SIZE = 10
SELECT_FIRST_SIZE = 6
CHUNK_1 = 1
CHUNK_2 = 2


def _main_ref(chat_id: int, message_id: int) -> dict[str, int]:
    return {"chat_id": int(chat_id), "message_id": int(message_id)}


def _total_pages(total: int) -> int:
    return max(1, (total + TEXT_PAGE_SIZE - 1) // TEXT_PAGE_SIZE)


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


def _parse_booking_id_legacy(data: str) -> int | None:
    parts = (data or "").split(":")
    if len(parts) != 4:  # noqa: PLR2004
        return None
    if parts[:2] != ["c", "booking"] or parts[3] != "cancel":
        return None
    try:
        return int(parts[2])
    except ValueError:
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


def _render_list_page_text(
    *,
    bookings: list[BookingForReview],
    page: int,
    total_pages: int,
    client_timezone: Timezone,
) -> str:
    page = _clamp_page(page, total_pages)
    start_index = (page - 1) * TEXT_PAGE_SIZE
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


def _kb_list(*, total_pages: int, page: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 1:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"{CB_PREFIX}l:p:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=f"{CB_PREFIX}noop"))
        if page < total_pages:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"{CB_PREFIX}l:p:{page + 1}"))
        rows.append(nav)

    rows.append([InlineKeyboardButton(text=txt.btn_select_mode(), callback_data=f"{CB_PREFIX}s:p:{page}:c:{CHUNK_1}")])
    rows.append([InlineKeyboardButton(text=btn_close(), callback_data=f"{CB_PREFIX}close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _select_slice(*, bookings: list[BookingForReview], page: int, chunk: int) -> tuple[list[BookingForReview], bool]:
    total_pages = _total_pages(len(bookings))
    page = _clamp_page(page, total_pages)
    base_start = (page - 1) * TEXT_PAGE_SIZE
    base_end = base_start + TEXT_PAGE_SIZE
    base = bookings[base_start:base_end]

    if chunk == CHUNK_2 and len(base) > SELECT_FIRST_SIZE:
        return base[SELECT_FIRST_SIZE:], False
    visible = base[:SELECT_FIRST_SIZE]
    has_more = len(base) > SELECT_FIRST_SIZE
    return visible, has_more


def _kb_select(
    *,
    bookings: list[BookingForReview],
    page: int,
    chunk: int,
    client_timezone: Timezone,
) -> InlineKeyboardMarkup:
    total_pages = _total_pages(len(bookings))
    page = _clamp_page(page, total_pages)
    chunk = CHUNK_2 if chunk == CHUNK_2 else CHUNK_1
    visible, has_more = _select_slice(bookings=bookings, page=page, chunk=chunk)

    rows: list[list[InlineKeyboardButton]] = []
    for booking in visible:
        label = _booking_button_label(booking, client_timezone=client_timezone)
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{CB_PREFIX}open:{booking.id}:p:{page}:c:{chunk}",
                ),
            ],
        )

    nav_row = _kb_select_nav_row(total_pages=total_pages, page=page)
    if nav_row is not None:
        rows.append(nav_row)

    toggle_row = _kb_select_chunk_toggle_row(has_more=has_more, page=page, chunk=chunk)
    if toggle_row is not None:
        rows.append(toggle_row)

    rows.append([InlineKeyboardButton(text=txt.btn_back_to_list(), callback_data=f"{CB_PREFIX}l:p:{page}")])
    rows.append([InlineKeyboardButton(text=btn_close(), callback_data=f"{CB_PREFIX}close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_select_nav_row(*, total_pages: int, page: int) -> list[InlineKeyboardButton] | None:
    if total_pages <= 1:
        return None
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"{CB_PREFIX}s:p:{page - 1}:c:{CHUNK_1}"))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=f"{CB_PREFIX}noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"{CB_PREFIX}s:p:{page + 1}:c:{CHUNK_1}"))
    return nav


def _kb_select_chunk_toggle_row(*, has_more: bool, page: int, chunk: int) -> list[InlineKeyboardButton] | None:
    if has_more and chunk == CHUNK_1:
        return [InlineKeyboardButton(text=txt.btn_more(), callback_data=f"{CB_PREFIX}s:p:{page}:c:{CHUNK_2}")]
    if chunk == CHUNK_2:
        return [InlineKeyboardButton(text=txt.btn_less(), callback_data=f"{CB_PREFIX}s:p:{page}:c:{CHUNK_1}")]
    return None


def _booking_button_label(booking: BookingForReview, *, client_timezone: Timezone) -> str:
    slot_str = to_zone(booking.start_at, client_timezone).strftime("%d.%m %H:%M")
    master_name = str(getattr(booking.master, "name", "")).strip() or "Мастер"
    badge = status_badge(booking.status)
    return f"{badge} {slot_str} • {master_name}".strip()


def _render_booking_card_text(*, booking: BookingForReview, client_timezone: Timezone) -> str:
    slot_client = to_zone(booking.start_at, client_timezone)
    badge = status_badge(booking.status)
    status_label = BOOKING_STATUS_MAP[booking.status]
    master_name_safe = html_escape(str(getattr(booking.master, "name", "")))

    return dedent(
        f"""
        <b>{txt.details_title()}</b>\n
        <b>{master_name_safe}</b>
        {badge} {status_label}

        📅 {slot_client:%d.%m.%Y}
        ⏰ {slot_client:%H:%M}
        ⏳ {int(booking.duration_min)} мин
        """,
    ).strip()


def _kb_booking_card(*, booking: BookingForReview, page: int, chunk: int, can_cancel: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    master_tg = int(getattr(booking.master, "telegram_id", 0) or 0)
    if master_tg > 0 and can_cancel:
        rows.append(
            [
                InlineKeyboardButton(text=txt.btn_write_master(), url=f"tg://user?id={master_tg}"),
                InlineKeyboardButton(
                    text=btn_cancel_booking(),
                    callback_data=f"{CB_PREFIX}cancel:{booking.id}:p:{page}:c:{chunk}",
                ),
            ],
        )
    elif master_tg > 0:
        rows.append([InlineKeyboardButton(text=txt.btn_write_master(), url=f"tg://user?id={master_tg}")])
    elif can_cancel:
        rows.append(
            [
                InlineKeyboardButton(
                    text=btn_cancel_booking(),
                    callback_data=f"{CB_PREFIX}cancel:{booking.id}:p:{page}:c:{chunk}",
                ),
            ],
        )

    rows.append([InlineKeyboardButton(text=btn_back(), callback_data=f"{CB_PREFIX}s:p:{page}:c:{chunk}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_cancel_confirm(*, booking_id: int, page: int, chunk: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=btn_cancel_yes(),
                    callback_data=f"{CB_PREFIX}cancel_yes:{booking_id}:p:{page}:c:{chunk}",
                ),
                InlineKeyboardButton(
                    text=btn_cancel_no(),
                    callback_data=f"{CB_PREFIX}cancel_no:{booking_id}:p:{page}:c:{chunk}",
                ),
            ],
        ],
    )


def _kb_cancel_ntf_confirm(*, booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=btn_cancel_yes(), callback_data=f"{CB_PREFIX}cancel_yes_ntf:{booking_id}"),
                InlineKeyboardButton(text=btn_cancel_no(), callback_data=f"{CB_PREFIX}cancel_no_ntf:{booking_id}"),
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
            limit=50,
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
    ev.info("client_list_bookings.start")

    fetched = await _fetch_client_bookings(telegram_id)
    if fetched is None:
        ev.warning("client_list_bookings.client_not_found")
        await message.answer(CLIENT_NOT_FOUND_MESSAGE)
        return

    client_timezone, all_bookings = fetched

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

    total_pages = _total_pages(len(all_bookings))
    page = 1

    if not all_bookings:
        ev.info("client_list_bookings.start_result", outcome="empty")
        sent = await message.answer(
            txt.empty_list(),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=btn_close(), callback_data=f"{CB_PREFIX}close")]],
            ),
            parse_mode="HTML",
        )
        await _set_main_message(state, chat_id=sent.chat.id, message_id=sent.message_id, page=page)
        await safe_delete(message, ev=ev, event="client_list_bookings.delete_menu_message_failed")
        return

    ev.info("client_list_bookings.start_result", outcome="listed", bookings_count=len(all_bookings))
    page_bookings = all_bookings[:TEXT_PAGE_SIZE]
    sent = await message.answer(
        _render_list_page_text(
            bookings=page_bookings,
            page=page,
            total_pages=total_pages,
            client_timezone=client_timezone,
        ),
        reply_markup=_kb_list(total_pages=total_pages, page=page),
        parse_mode="HTML",
    )
    await _set_main_message(state, chat_id=sent.chat.id, message_id=sent.message_id, page=page)
    await safe_delete(message, ev=ev, event="client_list_bookings.delete_menu_message_failed")


async def _load_and_validate_booking(
    *,
    telegram_id: int,
    booking_id: int,
) -> tuple[Timezone, BookingForReview] | None:
    async with session_local() as session:
        client_repo = ClientRepository(session)
        booking_repo = BookingRepository(session)
        try:
            client = await client_repo.get_by_telegram_id(telegram_id)
        except ClientNotFound:
            return None
        try:
            booking = await booking_repo.get_for_review(booking_id)
        except BookingNotFound:
            raise
        if booking.client.id != client.id:
            return None
    return client.timezone, booking


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

    total_pages = _total_pages(len(all_bookings))
    page = _clamp_page(page, total_pages)

    await callback.answer()
    if callback.message is None:
        return

    if not all_bookings:
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

    start = (page - 1) * TEXT_PAGE_SIZE
    page_bookings = all_bookings[start : start + TEXT_PAGE_SIZE]
    await safe_edit_text(
        callback.message,
        text=_render_list_page_text(
            bookings=page_bookings,
            page=page,
            total_pages=total_pages,
            client_timezone=client_timezone,
        ),
        reply_markup=_kb_list(total_pages=total_pages, page=page),
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


async def _render_select(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    page: int,
    chunk: int,
) -> None:
    fetched = await _fetch_client_bookings(callback.from_user.id)
    if fetched is None:
        await callback.answer(CLIENT_NOT_FOUND_MESSAGE, show_alert=True)
        return
    client_timezone, all_bookings = fetched

    await callback.answer()
    if callback.message is None:
        return

    if not all_bookings:
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

    total_pages = _total_pages(len(all_bookings))
    page = _clamp_page(page, total_pages)
    chunk = CHUNK_2 if chunk == CHUNK_2 else CHUNK_1
    await safe_edit_text(
        callback.message,
        text=txt.choose_title(page=page, total_pages=total_pages),
        reply_markup=_kb_select(bookings=all_bookings, page=page, chunk=chunk, client_timezone=client_timezone),
        parse_mode="HTML",
        ev=ev,
        event="client_list_bookings.edit_select_failed",
    )
    await _set_main_message(
        state,
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        page=page,
    )


async def _render_booking_card(
    *,
    callback: CallbackQuery,
    booking_id: int,
    page: int,
    chunk: int,
) -> None:
    try:
        loaded = await _load_and_validate_booking(telegram_id=callback.from_user.id, booking_id=booking_id)
    except BookingNotFound:
        await callback.answer(txt.booking_not_found(), show_alert=True)
        return
    if loaded is None:
        await callback.answer(txt.forbidden(), show_alert=True)
        return
    client_timezone, booking = loaded

    now = datetime.now(UTC)
    can_cancel = booking.status in BookingStatus.active() and booking.start_at > now

    await callback.answer()
    if callback.message is None:
        return
    await safe_edit_text(
        callback.message,
        text=_render_booking_card_text(booking=booking, client_timezone=client_timezone),
        reply_markup=_kb_booking_card(booking=booking, page=page, chunk=chunk, can_cancel=can_cancel),
        parse_mode="HTML",
        ev=ev,
        event="client_list_bookings.edit_card_failed",
    )


async def _delete_callback_message(callback: CallbackQuery, *, event: str) -> None:
    if callback.message is None:
        return
    deleted = await safe_delete(callback.message, ev=ev, event=event)
    if deleted:
        return
    await callback.message.edit_reply_markup(reply_markup=None)


async def _handle_close(*, callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    ev.info("client_list_bookings.close")
    await _delete_callback_message(callback, event="client_list_bookings.close_delete_failed")
    await _clear_main_message(state)


async def _handle_cancel_ntf_prompt(*, callback: CallbackQuery, booking_id: int) -> None:
    await callback.answer()
    ev.info("client_list_bookings.cancel_prompt", source="notification", booking_id=int(booking_id))
    if callback.message is None:
        return
    await safe_edit_text(
        callback.message,
        text=txt.cancel_confirm(),
        reply_markup=_kb_cancel_ntf_confirm(booking_id=booking_id),
        parse_mode="HTML",
        ev=ev,
        event="client_list_bookings.edit_cancel_ntf_confirm_failed",
    )


async def _handle_cancel_ntf_no(*, callback: CallbackQuery) -> None:
    await callback.answer()
    ev.info("client_list_bookings.cancel_no", source="notification")
    await _delete_callback_message(callback, event="client_list_bookings.cancel_ntf_no_delete_failed")


async def _handle_cancel_ntf_yes(*, callback: CallbackQuery, notifier: Notifier, booking_id: int) -> None:
    ev.info("client_list_bookings.cancel_yes", source="notification", booking_id=int(booking_id))
    booking = await _cancel_booking_and_notify(callback=callback, notifier=notifier, booking_id=booking_id)
    if booking is None:
        return
    await _delete_callback_message(callback, event="client_list_bookings.cancel_ntf_yes_delete_failed")


async def _handle_cancel_prompt(*, callback: CallbackQuery, booking_id: int, page: int, chunk: int) -> None:
    await callback.answer()
    ev.info("client_list_bookings.cancel_prompt", source="list", booking_id=int(booking_id))
    if callback.message is None:
        return
    await safe_edit_text(
        callback.message,
        text=txt.cancel_confirm(),
        reply_markup=_kb_cancel_confirm(booking_id=booking_id, page=page, chunk=chunk),
        parse_mode="HTML",
        ev=ev,
        event="client_list_bookings.edit_cancel_confirm_failed",
    )


async def _handle_cancel_no(*, callback: CallbackQuery, booking_id: int, page: int, chunk: int) -> None:
    ev.info("client_list_bookings.cancel_no", source="list", booking_id=int(booking_id))
    await _render_booking_card(callback=callback, booking_id=booking_id, page=page, chunk=chunk)


async def _handle_cancel_yes(
    *,
    callback: CallbackQuery,
    notifier: Notifier,
    booking_id: int,
    page: int,
    chunk: int,
) -> None:
    ev.info("client_list_bookings.cancel_yes", source="list", booking_id=int(booking_id))
    booking = await _cancel_booking_and_notify(callback=callback, notifier=notifier, booking_id=booking_id)
    if booking is None:
        return
    if callback.message is None:
        return
    await safe_edit_text(
        callback.message,
        text=txt.cancelled_text(),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=btn_back(), callback_data=f"{CB_PREFIX}s:p:{page}:c:{chunk}")]],
        ),
        parse_mode="HTML",
        ev=ev,
        event="client_list_bookings.edit_cancelled_failed",
    )


@dataclass(frozen=True, slots=True)
class _ParsedBookingCallback:
    name: str
    booking_id: int | None = None
    page: int | None = None
    chunk: int | None = None


def _parse_static_callback(data: str) -> _ParsedBookingCallback | None:
    if data == f"{CB_PREFIX}noop":
        return _ParsedBookingCallback(name="noop")
    if data == f"{CB_PREFIX}close":
        return _ParsedBookingCallback(name="close")
    return None


def _parse_list_page_callback(data: str) -> _ParsedBookingCallback | None:
    # c:bookings:l:p:<page>
    parts = (data or "").split(":")
    if len(parts) != 5:  # noqa: PLR2004
        return None
    if parts[:3] != ["c", "bookings", "l"] or parts[3] != "p":
        return None
    try:
        page = int(parts[4])
    except ValueError:
        return None
    return _ParsedBookingCallback(name="list", page=page)


def _parse_select_page_callback(data: str) -> _ParsedBookingCallback | None:
    # c:bookings:s:p:<page>:c:<chunk>
    parts = (data or "").split(":")
    if len(parts) != 7:  # noqa: PLR2004
        return None
    if parts[:3] != ["c", "bookings", "s"] or parts[3] != "p" or parts[5] != "c":
        return None
    try:
        page = int(parts[4])
        chunk = int(parts[6])
    except ValueError:
        return None
    return _ParsedBookingCallback(name="select", page=page, chunk=chunk)


def _parse_booking_action_short_callback(data: str) -> _ParsedBookingCallback | None:
    # c:bookings:<action>:<booking_id>
    parts = (data or "").split(":")
    if len(parts) != 4:  # noqa: PLR2004
        return None
    if parts[:2] != ["c", "bookings"]:
        return None
    action = parts[2]
    if action not in {
        "open",
        "cancel",
        "cancel_yes",
        "cancel_no",
        "cancel_ntf",
        "cancel_yes_ntf",
        "cancel_no_ntf",
    }:
        return None
    try:
        booking_id = int(parts[3])
    except ValueError:
        return None
    return _ParsedBookingCallback(name=action, booking_id=booking_id)


def _parse_booking_action_context_callback(data: str) -> _ParsedBookingCallback | None:
    # c:bookings:<action>:<booking_id>:p:<page>:c:<chunk>
    parts = (data or "").split(":")
    if len(parts) != 8:  # noqa: PLR2004
        return None
    if parts[:2] != ["c", "bookings"]:
        return None
    action = parts[2]
    if action not in {"open", "cancel", "cancel_yes", "cancel_no"}:
        return None
    if parts[4] != "p" or parts[6] != "c":
        return None
    try:
        booking_id = int(parts[3])
        page = int(parts[5])
        chunk = int(parts[7])
    except ValueError:
        return None
    return _ParsedBookingCallback(name=action, booking_id=booking_id, page=page, chunk=chunk)


def _parse_legacy_callback(data: str) -> _ParsedBookingCallback | None:
    parts = (data or "").split(":")
    if parts[:2] != ["c", "bookings"]:
        return None

    if len(parts) == 3 and parts[2] == "back":  # noqa: PLR2004
        return _ParsedBookingCallback(name="back")
    if len(parts) == 4 and parts[2] == "page":  # noqa: PLR2004
        try:
            page = int(parts[3])
        except ValueError:
            return None
        return _ParsedBookingCallback(name="list", page=page)
    return None


def _parse_bookings_callback(data: str) -> _ParsedBookingCallback | None:
    for parser in (
        _parse_static_callback,
        _parse_list_page_callback,
        _parse_select_page_callback,
        _parse_booking_action_context_callback,
        _parse_booking_action_short_callback,
        _parse_legacy_callback,
    ):
        parsed = parser(data)
        if parsed is not None:
            return parsed
    return None


async def _resolve_page_chunk(*, state: FSMContext, page: int | None, chunk: int | None) -> tuple[int, int]:
    if page is None:
        page = int((await state.get_data()).get(LIST_BOOKINGS_PAGE_KEY, 1))
    if chunk is None:
        chunk = CHUNK_1
    chunk = CHUNK_2 if chunk == CHUNK_2 else CHUNK_1
    return int(page), int(chunk)


async def _cb_noop(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    parsed: _ParsedBookingCallback,
) -> None:
    del state, notifier, parsed
    await callback.answer()


async def _cb_close(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    parsed: _ParsedBookingCallback,
) -> None:
    del notifier, parsed
    await _handle_close(callback=callback, state=state)


async def _cb_list(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    parsed: _ParsedBookingCallback,
) -> None:
    del notifier
    if parsed.page is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return
    await _render_list(callback=callback, state=state, page=parsed.page)


async def _cb_select(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    parsed: _ParsedBookingCallback,
) -> None:
    del notifier
    if parsed.page is None or parsed.chunk is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return
    await _render_select(callback=callback, state=state, page=parsed.page, chunk=parsed.chunk)


async def _cb_open(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    parsed: _ParsedBookingCallback,
) -> None:
    del notifier
    if parsed.booking_id is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return
    page, chunk = await _resolve_page_chunk(state=state, page=parsed.page, chunk=parsed.chunk)
    await _render_booking_card(callback=callback, booking_id=parsed.booking_id, page=page, chunk=chunk)


async def _cb_cancel_ntf_prompt(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    parsed: _ParsedBookingCallback,
) -> None:
    del state, notifier
    if parsed.booking_id is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return
    await _handle_cancel_ntf_prompt(callback=callback, booking_id=parsed.booking_id)


async def _cb_cancel_ntf_yes(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    parsed: _ParsedBookingCallback,
) -> None:
    del state
    if parsed.booking_id is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return
    await _handle_cancel_ntf_yes(callback=callback, notifier=notifier, booking_id=parsed.booking_id)


async def _cb_cancel_ntf_no(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    parsed: _ParsedBookingCallback,
) -> None:
    del state, notifier, parsed
    await _handle_cancel_ntf_no(callback=callback)


async def _cb_cancel_prompt(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    parsed: _ParsedBookingCallback,
) -> None:
    del notifier
    if parsed.booking_id is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return
    page, chunk = await _resolve_page_chunk(state=state, page=parsed.page, chunk=parsed.chunk)
    await _handle_cancel_prompt(callback=callback, booking_id=parsed.booking_id, page=page, chunk=chunk)


async def _cb_cancel_yes(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    parsed: _ParsedBookingCallback,
) -> None:
    if parsed.booking_id is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return
    page, chunk = await _resolve_page_chunk(state=state, page=parsed.page, chunk=parsed.chunk)
    await _handle_cancel_yes(
        callback=callback,
        notifier=notifier,
        booking_id=parsed.booking_id,
        page=page,
        chunk=chunk,
    )


async def _cb_cancel_no(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    parsed: _ParsedBookingCallback,
) -> None:
    del notifier
    if parsed.booking_id is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return
    page, chunk = await _resolve_page_chunk(state=state, page=parsed.page, chunk=parsed.chunk)
    await _handle_cancel_no(callback=callback, booking_id=parsed.booking_id, page=page, chunk=chunk)


async def _cb_back(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    parsed: _ParsedBookingCallback,
) -> None:
    del notifier, parsed
    page, _ = await _resolve_page_chunk(state=state, page=None, chunk=None)
    await _render_list(callback=callback, state=state, page=page)


_BOOKINGS_CALLBACK_HANDLERS = {
    "noop": _cb_noop,
    "close": _cb_close,
    "list": _cb_list,
    "select": _cb_select,
    "open": _cb_open,
    "cancel_ntf": _cb_cancel_ntf_prompt,
    "cancel_yes_ntf": _cb_cancel_ntf_yes,
    "cancel_no_ntf": _cb_cancel_ntf_no,
    "cancel": _cb_cancel_prompt,
    "cancel_yes": _cb_cancel_yes,
    "cancel_no": _cb_cancel_no,
    "back": _cb_back,
}


async def _dispatch_bookings_callback(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    parsed: _ParsedBookingCallback,
) -> None:
    handler = _BOOKINGS_CALLBACK_HANDLERS.get(parsed.name)
    if handler is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return
    await handler(callback=callback, state=state, notifier=notifier, parsed=parsed)


@router.callback_query(UserRole(ActiveRole.CLIENT), F.data.startswith(CB_PREFIX))
async def client_bookings_callbacks(
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="client_list_bookings", step="callbacks")
    if not await rate_limit_callback(callback, rate_limiter, name="client_list_bookings:callbacks", ttl_sec=1):
        return

    parsed = _parse_bookings_callback(callback.data or "")
    if parsed is None:
        await callback.answer(txt.invalid_command(), show_alert=True)
        return
    await _dispatch_bookings_callback(callback=callback, state=state, notifier=notifier, parsed=parsed)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith(CB_PREFIX))
async def client_bookings_callbacks_in_master_role(
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    rate_limiter: RateLimiter | None = None,
) -> None:
    """
    Allow client booking actions (e.g. cancel from notification) even when the user is
    currently in the MASTER role.
    """
    await client_bookings_callbacks(
        callback=callback,
        state=state,
        notifier=notifier,
        rate_limiter=rate_limiter,
    )


async def _cancel_booking_and_notify(
    *,
    callback: CallbackQuery,
    notifier: Notifier,
    booking_id: int,
) -> BookingForReview | None:
    ev.info(
        "booking.cancel_attempt",
        actor="client",
        booking_id=int(booking_id),
        client_telegram_id=int(callback.from_user.id),
    )
    async with active_session() as session:
        client_repo = ClientRepository(session)
        booking_repo = BookingRepository(session)
        try:
            client = await client_repo.get_by_telegram_id(callback.from_user.id)
        except ClientNotFound:
            ev.warning("client_list_bookings.client_not_found")
            ev.info(
                "booking.cancel_rejected",
                actor="client",
                booking_id=int(booking_id),
                client_telegram_id=int(callback.from_user.id),
                error="client_not_found",
            )
            await callback.answer(CLIENT_NOT_FOUND_MESSAGE, show_alert=True)
            return None
        try:
            booking = await booking_repo.get_for_review(booking_id)
        except BookingNotFound:
            ev.warning("client_list_bookings.booking_not_found", booking_id=int(booking_id))
            ev.info(
                "booking.cancel_rejected",
                actor="client",
                booking_id=int(booking_id),
                client_id=int(client.id),
                error="booking_not_found",
            )
            await callback.answer(txt.booking_not_found(), show_alert=True)
            return None

        if booking.client.id != client.id:
            ev.warning("client_list_bookings.forbidden", booking_id=int(booking_id))
            master_id = getattr(booking.master, "id", None)
            ev.info(
                "booking.cancel_rejected",
                actor="client",
                booking_id=int(booking_id),
                master_id=int(master_id) if master_id is not None else None,
                client_id=int(client.id),
                error="forbidden",
            )
            await callback.answer(txt.forbidden(), show_alert=True)
            return None

        cancelled = await booking_repo.cancel_by_client(booking_id=booking_id, client_id=client.id)

    if not cancelled:
        ev.info("client_list_bookings.cannot_cancel", booking_id=int(booking_id))
        ev.info(
            "booking.cancel_rejected",
            actor="client",
            booking_id=int(booking_id),
            client_id=int(client.id),
            error="cannot_cancel",
        )
        await callback.answer(txt.cannot_cancel(), show_alert=True)
        return None

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

    master_id = getattr(booking.master, "id", None)
    ev.info(
        "booking.cancelled",
        actor="client",
        booking_id=int(booking.id),
        master_id=int(master_id) if master_id is not None else None,
        client_id=int(client.id),
    )
    ev.info(
        "client_list_bookings.cancelled",
        booking_id=int(booking.id),
        master_id=int(master_id) if master_id is not None else None,
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
    booking_id = _parse_booking_id_legacy(callback.data or "")
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


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith("c:booking:"))
async def client_cancel_booking_legacy_in_master_role(
    callback: CallbackQuery,
    notifier: Notifier,
    rate_limiter: RateLimiter | None = None,
) -> None:
    await client_cancel_booking_legacy(callback=callback, notifier=notifier, rate_limiter=rate_limiter)
