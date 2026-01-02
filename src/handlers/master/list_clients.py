from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from html import escape as html_escape
from math import ceil

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from src.core.sa import active_session, session_local
from src.datetime_utils import to_zone
from src.handlers.shared.ui import safe_delete, safe_edit_reply_markup, safe_edit_text
from src.models import Booking as BookingEntity
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.repositories import MasterNotFound, MasterRepository
from src.schemas import MasterWithClients
from src.schemas.enums import AttendanceOutcome, status_badge
from src.texts import common as common_txt, master_list_clients as txt
from src.texts.buttons import btn_back, btn_close
from src.texts.master_client_card import ClientHints, ClientSummary, card as render_client_card
from src.utils import format_phone_display, format_phone_e164

ev = EventLogger(__name__)
router = Router(name=__name__)

# Pagination model:
# - Logical page is always based on 10 items (for consistency).
# - Text list shows full page (10 items).
# - Select-mode shows first 6 items of the same logical page
#   to keep UI compact and avoid scrolling.
# - Navigation arrows always move by logical page (±10).
TEXT_LIST_PAGE_SIZE = 10
BUTTON_SELECT_PAGE_SIZE = 6
BUTTON_SELECT_CHUNK_2_SIZE = 4
BUTTON_SELECT_CHUNK_1 = 1
BUTTON_SELECT_CHUNK_2 = 2
CLIENTS_PAGE_PREFIX = "master_clients_page:"

CLIENTS_CB_PREFIX = "m:cl:"
CLIENTS_CARD_PREFIX = f"{CLIENTS_CB_PREFIX}c:"


def _parse_clients_page(callback_data: str | None) -> int | str | None:
    """
    Returns:
    - "close" for close action
    - int page number for pagination
    - None for invalid callback data
    """
    data = (callback_data or "").removeprefix(CLIENTS_PAGE_PREFIX)
    if not data:
        return None
    if data == "close":
        return "close"
    try:
        return int(data)
    except ValueError:
        return None


def _parse_int_suffix(prefix: str, data: str | None) -> int | None:
    raw = data or ""
    if not raw.startswith(prefix):
        return None
    try:
        return int(raw[len(prefix) :])
    except ValueError:
        return None


def _parse_open_action(data: str | None) -> tuple[int, int, int] | None:
    parts = (data or "").split(":")
    # m:cl:s:open:<client_id>:p:<page>:c:<chunk>
    if len(parts) != 9:  # noqa: PLR2004
        return None
    if parts[:4] != ["m", "cl", "s", "open"] or parts[5] != "p" or parts[7] != "c":  # noqa: PLR2004
        return None
    try:
        client_id = int(parts[4])
        page = int(parts[6])
        chunk = int(parts[8])
    except ValueError:
        return None
    if chunk not in {BUTTON_SELECT_CHUNK_1, BUTTON_SELECT_CHUNK_2}:
        return None
    return client_id, page, chunk


def _parse_select_page(data: str | None) -> tuple[int, int] | None:
    parts = (data or "").split(":")
    # m:cl:s:p:<page>:c:<chunk>
    if len(parts) != 7:  # noqa: PLR2004
        return None
    if parts[:4] != ["m", "cl", "s", "p"] or parts[5] != "c":  # noqa: PLR2004
        return None
    try:
        page = int(parts[4])
        chunk = int(parts[6])
    except ValueError:
        return None
    if chunk not in {BUTTON_SELECT_CHUNK_1, BUTTON_SELECT_CHUNK_2}:
        return None
    return page, chunk


def _parse_card_action(action: str, data: str | None) -> tuple[int, int, int] | None:
    parts = (data or "").split(":")
    # m:cl:c:<action>:<client_id>:p:<page>:c:<chunk>
    if len(parts) != 9:  # noqa: PLR2004
        return None
    if parts[:3] != ["m", "cl", "c"] or parts[3] != action or parts[5] != "p" or parts[7] != "c":  # noqa: PLR2004
        return None
    try:
        client_id = int(parts[4])
        page = int(parts[6])
        chunk = int(parts[8])
    except ValueError:
        return None
    if chunk not in {BUTTON_SELECT_CHUNK_1, BUTTON_SELECT_CHUNK_2}:
        return None
    return client_id, page, chunk


@dataclass(frozen=True)
class ClientStats:
    last_visit_at_utc: datetime | None
    visits_count: int
    no_show_count: int


async def _fetch_master_with_clients(telegram_id: int) -> MasterWithClients:
    async with active_session(begin=False) as session:
        return await MasterRepository(session).get_with_clients_by_telegram_id(telegram_id)


async def _fetch_master_clients(telegram_id: int) -> Sequence:
    try:
        master = await _fetch_master_with_clients(telegram_id)
    except MasterNotFound:
        ev.warning("master_list_clients.master_not_found", telegram_id=telegram_id)
        return []
    return master.clients


def _render_client_line(client, *, index: int) -> str:
    name_raw = getattr(client, "name", None) or common_txt.label_default_client()
    name = html_escape(str(name_raw))
    phone = getattr(client, "phone", None)
    phone_part = f"{txt.phone_sep()}{html_escape(str(phone))}" if phone else ""
    offline_badge = common_txt.label_offline_badge() if getattr(client, "telegram_id", None) is None else ""
    return f"{index}. {name}{phone_part}{offline_badge}"


def _render_client_label(client) -> str:
    name_raw = getattr(client, "name", None) or common_txt.label_default_client()
    name = html_escape(str(name_raw))
    phone = getattr(client, "phone", None)
    phone_part = f"{txt.phone_sep()}{html_escape(format_phone_display(str(phone)))}" if phone else ""
    offline_badge = common_txt.label_offline_badge() if getattr(client, "telegram_id", None) is None else ""
    return f"{name}{phone_part}{offline_badge}".strip()


def _placeholder_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text=txt.btn_placeholder(), callback_data="m:noop")


def _nav_button(*, direction: str, mode: str, page: int, total_pages: int) -> InlineKeyboardButton:
    if direction == "prev":
        if page <= 1:
            return _placeholder_button()
        cb = f"{CLIENTS_CB_PREFIX}{mode}:p:{page - 1}"
        if mode == "s":
            cb = f"{cb}:c:1"
        return InlineKeyboardButton(text="⬅️", callback_data=cb)
    if direction == "next":
        if page >= total_pages:
            return _placeholder_button()
        cb = f"{CLIENTS_CB_PREFIX}{mode}:p:{page + 1}"
        if mode == "s":
            cb = f"{cb}:c:1"
        return InlineKeyboardButton(text="➡️", callback_data=cb)
    raise ValueError("Unsupported direction.")


def _build_list_menu_keyboard(*, page: int, total_pages: int) -> InlineKeyboardMarkup:
    nav_row: list[list[InlineKeyboardButton]] = []
    if total_pages > 1:
        nav_row = [
            [
                _nav_button(direction="prev", mode="l", page=page, total_pages=total_pages),
                _nav_button(direction="next", mode="l", page=page, total_pages=total_pages),
            ],
        ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            *nav_row,
            [
                InlineKeyboardButton(text=txt.btn_find(), callback_data="m:clients:search"),
                InlineKeyboardButton(text=txt.btn_select(), callback_data=f"{CLIENTS_CB_PREFIX}l:select:{page}"),
                InlineKeyboardButton(text=txt.btn_add(), callback_data="m:clients:add"),
            ],
            [InlineKeyboardButton(text=btn_back(), callback_data=f"{CLIENTS_CB_PREFIX}l:menu")],
            [InlineKeyboardButton(text=btn_close(), callback_data="m:close")],
        ],
    )


def _chunk_range(*, total_items: int, page: int, chunk: int) -> tuple[int, int]:
    base = (page - 1) * TEXT_LIST_PAGE_SIZE
    if chunk == BUTTON_SELECT_CHUNK_1:
        start = base
        end = base + BUTTON_SELECT_PAGE_SIZE
    else:
        start = base + BUTTON_SELECT_PAGE_SIZE
        end = base + TEXT_LIST_PAGE_SIZE
    start_num = start + 1
    end_num = min(end, total_items)
    return start_num, end_num


def _build_select_menu_keyboard(
    *,
    page: int,
    chunk: int,
    total_pages: int,
    total_items: int,
) -> list[list[InlineKeyboardButton]]:
    if total_pages > 1:
        row = [
            _nav_button(direction="prev", mode="s", page=page, total_pages=total_pages),
            InlineKeyboardButton(text=txt.btn_find(), callback_data="m:clients:search"),
            _nav_button(direction="next", mode="s", page=page, total_pages=total_pages),
        ]
    else:
        row = [InlineKeyboardButton(text=txt.btn_find(), callback_data="m:clients:search")]
    toggle_row: list[InlineKeyboardButton] = []
    base = (page - 1) * TEXT_LIST_PAGE_SIZE
    has_chunk2 = total_items > (base + BUTTON_SELECT_PAGE_SIZE)
    if chunk == BUTTON_SELECT_CHUNK_1 and has_chunk2:
        start_num, end_num = _chunk_range(total_items=total_items, page=page, chunk=BUTTON_SELECT_CHUNK_2)
        toggle_row.append(
            InlineKeyboardButton(
                text=f"Ещё ({start_num}–{end_num})",
                callback_data=f"{CLIENTS_CB_PREFIX}s:p:{page}:c:{BUTTON_SELECT_CHUNK_2}",
            ),
        )
    elif chunk == BUTTON_SELECT_CHUNK_2:
        start_num, end_num = _chunk_range(total_items=total_items, page=page, chunk=BUTTON_SELECT_CHUNK_1)
        toggle_row.append(
            InlineKeyboardButton(
                text=f"Назад ({start_num}–{end_num})",
                callback_data=f"{CLIENTS_CB_PREFIX}s:p:{page}:c:{BUTTON_SELECT_CHUNK_1}",
            ),
        )

    rows: list[list[InlineKeyboardButton]] = [row]
    if toggle_row:
        rows.append(toggle_row)
    rows.append([InlineKeyboardButton(text=txt.btn_back_to_list(), callback_data=f"{CLIENTS_CB_PREFIX}s:back:{page}")])
    rows.append([InlineKeyboardButton(text=btn_close(), callback_data="m:close")])
    return rows


def _build_select_keyboard(
    all_clients: Sequence,
    *,
    page: int,
    chunk: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    # NOTE: select-mode uses a smaller page size but keeps the same logical offset.
    base = (page - 1) * TEXT_LIST_PAGE_SIZE
    if chunk == BUTTON_SELECT_CHUNK_1:
        start = base
        end = base + BUTTON_SELECT_PAGE_SIZE
    else:
        start = base + BUTTON_SELECT_PAGE_SIZE
        end = base + TEXT_LIST_PAGE_SIZE
    clients_page = all_clients[start:end]

    rows: list[list[InlineKeyboardButton]] = []
    for client in clients_page:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_render_client_label(client),
                    callback_data=f"{CLIENTS_CB_PREFIX}s:open:{int(client.id)}:p:{page}:c:{chunk}",
                ),
            ],
        )
    rows.extend(
        _build_select_menu_keyboard(
            page=page,
            chunk=chunk,
            total_pages=total_pages,
            total_items=len(all_clients),
        ),
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_page_payload(
    all_clients: Sequence,
    *,
    page: int,
) -> tuple[str, InlineKeyboardMarkup, int] | None:
    total_pages = max(1, ceil(len(all_clients) / TEXT_LIST_PAGE_SIZE))
    if page < 1 or page > total_pages:
        ev.debug(
            "master_list_clients.callback_invalid",
            reason="page_out_of_range",
            page=page,
            total_pages=total_pages,
        )
        return None

    start = (page - 1) * TEXT_LIST_PAGE_SIZE
    end = start + TEXT_LIST_PAGE_SIZE
    clients_page = all_clients[start:end]

    text = _build_clients_page_text(clients_page, page, total_pages, start_index=start)
    keyboard = _build_list_menu_keyboard(page=page, total_pages=total_pages)
    return text, keyboard, total_pages


async def _close_clients_list(message) -> None:
    ok = await safe_delete(message, ev=ev, event="master_list_clients.close_delete_failed")
    if not ok:
        await safe_edit_reply_markup(
            message,
            reply_markup=None,
            ev=ev,
            event="master_list_clients.close_disable_keyboard_failed",
        )


async def _fetch_master_or_alert(callback: CallbackQuery, *, telegram_id: int) -> MasterWithClients | None:
    try:
        return await _fetch_master_with_clients(telegram_id)
    except MasterNotFound:
        await callback.answer(common_txt.generic_error(), show_alert=True)
        return None
    except Exception as exc:
        await ev.aexception(
            "master_list_clients.fetch_failed",
            stage="db",
            exc=exc,
        )
        await callback.answer(common_txt.generic_error(), show_alert=True)
        return None


def _build_clients_page_text(
    clients: Sequence,
    page: int,
    total_pages: int,
    *,
    start_index: int = 0,
) -> str:
    if not clients:
        return txt.empty_short()

    lines: list[str] = [
        txt.title(page=page, total_pages=total_pages),
        "",
        txt.offline_legend(),
        "",
    ]

    for offset, client in enumerate(clients, start=1):
        lines.append(_render_client_line(client, index=start_index + offset))

    return "\n".join(lines)


def _build_clients_pagination_keyboard(
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []

    # Pagination is only needed if there is more than one page.
    if total_pages > 1:
        nav_row: list[InlineKeyboardButton] = []

        if page > 1:
            nav_row.append(
                InlineKeyboardButton(
                    text=txt.btn_prev(),
                    callback_data=f"{CLIENTS_PAGE_PREFIX}{page - 1}",
                ),
            )

        if page < total_pages:
            nav_row.append(
                InlineKeyboardButton(
                    text=txt.btn_next(),
                    callback_data=f"{CLIENTS_PAGE_PREFIX}{page + 1}",
                ),
            )

        if nav_row:
            buttons.append(nav_row)

    # Close list button.
    buttons.append(
        [
            InlineKeyboardButton(
                text=txt.btn_close(),
                callback_data=f"{CLIENTS_PAGE_PREFIX}close",
            ),
        ],
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _build_empty_clients_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=btn_back(), callback_data=f"{CLIENTS_CB_PREFIX}l:menu")],
            [InlineKeyboardButton(text=btn_close(), callback_data="m:close")],
        ],
    )


async def start_clients_entry(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_list_clients", step="start")
    telegram_id = callback.from_user.id
    master = await _fetch_master_or_alert(callback, telegram_id=telegram_id)
    if master is None:
        return

    all_clients = master.clients
    if not all_clients:
        if callback.message is not None:
            await safe_edit_text(
                callback.message,
                text=txt.empty_long(),
                reply_markup=_build_empty_clients_keyboard(),
                ev=ev,
                event="master_list_clients.edit_empty_failed",
            )
        return

    page = 1
    total_pages = max(1, ceil(len(all_clients) / TEXT_LIST_PAGE_SIZE))

    start = (page - 1) * TEXT_LIST_PAGE_SIZE
    end = start + TEXT_LIST_PAGE_SIZE
    clients_page = all_clients[start:end]

    text = _build_clients_page_text(clients_page, page, total_pages, start_index=start)
    keyboard = _build_list_menu_keyboard(page=page, total_pages=total_pages)
    if callback.message is not None:
        ok = await safe_edit_text(
            callback.message,
            text=text,
            reply_markup=keyboard,
            ev=ev,
            event="master_list_clients.edit_failed",
        )
        if ok:
            return
    await callback.bot.send_message(chat_id=callback.from_user.id, text=text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith(CLIENTS_PAGE_PREFIX))
async def master_clients_pagination(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_list_clients", step="pagination")
    await callback.answer()

    action = _parse_clients_page(callback.data)
    if action is None:
        ev.debug("master_list_clients.callback_invalid", reason="bad_callback", data=callback.data)
        return
    if action == "close":
        await _close_clients_list(callback.message)
        return

    page = int(action)

    telegram_id = callback.from_user.id
    master = await _fetch_master_or_alert(callback, telegram_id=telegram_id)
    if master is None:
        return

    all_clients = master.clients
    if not all_clients:
        await safe_edit_text(
            callback.message,
            text=txt.no_clients_now(),
            reply_markup=_build_empty_clients_keyboard(),
            ev=ev,
            event="master_list_clients.edit_failed",
        )
        return

    payload = _build_page_payload(all_clients, page=page)
    if payload is None:
        return
    text, keyboard, _ = payload
    await safe_edit_text(
        callback.message,
        text=text,
        reply_markup=keyboard,
        ev=ev,
        event="master_list_clients.edit_failed",
    )


async def _edit_to_clients_menu(message) -> None:
    from src.handlers.master.master_menu import build_master_clients_keyboard
    from src.texts import master_menu as master_menu_txt

    await safe_edit_text(
        message,
        text=master_menu_txt.choose_action(),
        reply_markup=build_master_clients_keyboard(),
        ev=ev,
        event="master_list_clients.back_menu_edit_failed",
    )


async def _edit_list_page(message, *, all_clients: Sequence, page: int) -> None:
    payload = _build_page_payload(all_clients, page=page)
    if payload is None:
        return
    text, keyboard, _ = payload
    await safe_edit_text(
        message,
        text=text,
        reply_markup=keyboard,
        ev=ev,
        event="master_list_clients.edit_failed",
    )


async def _edit_select_page(message, *, all_clients: Sequence, page: int, chunk: int, total_pages: int) -> None:
    await safe_edit_text(
        message,
        text=f"{txt.choose_title(page=page, total_pages=total_pages)}\n\n{txt.offline_legend()}",
        reply_markup=_build_select_keyboard(all_clients, page=page, chunk=chunk, total_pages=total_pages),
        ev=ev,
        event="master_list_clients.select_edit_failed",
    )


async def _fetch_client_stats(*, master_id: int, client_id: int) -> ClientStats:
    async with session_local() as session:
        stmt = select(
            func.max(BookingEntity.start_at).filter(BookingEntity.attendance_outcome == AttendanceOutcome.ATTENDED),
            func.count().filter(BookingEntity.attendance_outcome == AttendanceOutcome.ATTENDED),
            func.count().filter(BookingEntity.attendance_outcome == AttendanceOutcome.NO_SHOW),
        ).where(
            BookingEntity.master_id == master_id,
            BookingEntity.client_id == client_id,
        )
        row = (await session.execute(stmt)).one()
        last_visit_at, visits_count, no_show_count = row
        return ClientStats(
            last_visit_at_utc=last_visit_at,
            visits_count=int(visits_count or 0),
            no_show_count=int(no_show_count or 0),
        )


def _card_action_cb(action: str, *, client_id: int, page: int, chunk: int) -> str:
    return f"{CLIENTS_CARD_PREFIX}{action}:{int(client_id)}:p:{int(page)}:c:{int(chunk)}"


def _kb_client_card(*, client_id: int, page: int, chunk: int, telegram_id: int | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if telegram_id is not None:
        rows.append([InlineKeyboardButton(text="💬 Написать в Telegram", url=f"tg://user?id={int(telegram_id)}")])
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="➕ Записать клиента",
                    callback_data=_card_action_cb("book", client_id=client_id, page=page, chunk=chunk),
                ),
                InlineKeyboardButton(
                    text="📅 История записей",
                    callback_data=_card_action_cb("history", client_id=client_id, page=page, chunk=chunk),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Редактировать клиента",
                    callback_data=f"m:edit_client:open:{int(client_id)}:p:{int(page)}:c:{int(chunk)}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=txt.btn_back_to_choose(),
                    callback_data=f"{CLIENTS_CB_PREFIX}s:p:{int(page)}:c:{int(chunk)}",
                ),
            ],
            [InlineKeyboardButton(text=btn_close(), callback_data="m:close")],
        ],
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _edit_client_card(message, *, master: MasterWithClients, client_id: int, page: int, chunk: int) -> bool:
    client = next((c for c in master.clients if int(c.id) == int(client_id)), None)
    if client is None:
        return False

    stats = await _fetch_client_stats(master_id=int(master.id), client_id=int(client_id))
    last_visit_day = stats.last_visit_at_utc.date() if stats.last_visit_at_utc is not None else None

    is_offline = client.telegram_id is None
    phone_display = format_phone_e164(str(client.phone)) if getattr(client, "phone", None) else None
    name_safe = html_escape(str(getattr(client, "name", None) or common_txt.label_default_client()))

    text = render_client_card(
        name=name_safe,
        is_offline=bool(is_offline),
        phone=phone_display,
        summary=ClientSummary(
            last_visit_day=last_visit_day,
            total_visits=int(stats.visits_count),
            no_show=int(stats.no_show_count),
        ),
        hints=ClientHints(show_offline_hint=True, show_noshow_hint=True),
    )

    await safe_edit_text(
        message,
        text=text,
        reply_markup=_kb_client_card(
            client_id=int(client_id),
            page=int(page),
            chunk=int(chunk),
            telegram_id=client.telegram_id,
        ),
        ev=ev,
        event="master_list_clients.card_edit_failed",
    )
    return True


@router.callback_query(F.data == f"{CLIENTS_CB_PREFIX}l:menu")
async def master_clients_back_to_menu(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_list_clients", step="back_menu")
    await callback.answer()
    if callback.message is not None:
        await _edit_to_clients_menu(callback.message)


@router.callback_query(F.data.startswith(f"{CLIENTS_CB_PREFIX}l:p:"))
async def master_clients_list_page(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_list_clients", step="list_page")
    await callback.answer()
    if callback.message is None:
        return
    page = _parse_int_suffix(f"{CLIENTS_CB_PREFIX}l:p:", callback.data)
    if page is None:
        return

    telegram_id = callback.from_user.id
    master = await _fetch_master_or_alert(callback, telegram_id=telegram_id)
    if master is None:
        await safe_edit_text(
            callback.message,
            text=txt.no_clients_now(),
            reply_markup=_build_empty_clients_keyboard(),
            ev=ev,
            event="master_list_clients.edit_failed",
        )
        return
    all_clients = master.clients
    if not all_clients:
        await safe_edit_text(
            callback.message,
            text=txt.no_clients_now(),
            reply_markup=_build_empty_clients_keyboard(),
            ev=ev,
            event="master_list_clients.edit_failed",
        )
        return

    total_pages = max(1, ceil(len(all_clients) / TEXT_LIST_PAGE_SIZE))
    await _edit_list_page(callback.message, all_clients=all_clients, page=max(1, min(page, total_pages)))


@router.callback_query(F.data.startswith(f"{CLIENTS_CB_PREFIX}l:select:"))
async def master_clients_select_mode(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_list_clients", step="select_start")
    await callback.answer()
    if callback.message is None:
        return
    page = _parse_int_suffix(f"{CLIENTS_CB_PREFIX}l:select:", callback.data)
    if page is None:
        return

    telegram_id = callback.from_user.id
    master = await _fetch_master_or_alert(callback, telegram_id=telegram_id)
    if master is None:
        await safe_edit_text(
            callback.message,
            text=txt.no_clients_now(),
            reply_markup=_build_empty_clients_keyboard(),
            ev=ev,
            event="master_list_clients.edit_failed",
        )
        return
    all_clients = master.clients
    if not all_clients:
        await safe_edit_text(
            callback.message,
            text=txt.no_clients_now(),
            reply_markup=_build_empty_clients_keyboard(),
            ev=ev,
            event="master_list_clients.edit_failed",
        )
        return

    total_pages = max(1, ceil(len(all_clients) / TEXT_LIST_PAGE_SIZE))
    await _edit_select_page(
        callback.message,
        all_clients=all_clients,
        page=max(1, min(page, total_pages)),
        chunk=1,
        total_pages=total_pages,
    )


@router.callback_query(F.data.startswith(f"{CLIENTS_CB_PREFIX}s:p:"))
async def master_clients_select_page(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_list_clients", step="select_page")
    await callback.answer()
    if callback.message is None:
        return
    parsed = _parse_select_page(callback.data)
    if parsed is None:
        return
    page, chunk = parsed

    telegram_id = callback.from_user.id
    master = await _fetch_master_or_alert(callback, telegram_id=telegram_id)
    if master is None:
        await safe_edit_text(
            callback.message,
            text=txt.no_clients_now(),
            reply_markup=_build_empty_clients_keyboard(),
            ev=ev,
            event="master_list_clients.edit_failed",
        )
        return
    all_clients = master.clients
    if not all_clients:
        await safe_edit_text(
            callback.message,
            text=txt.no_clients_now(),
            reply_markup=_build_empty_clients_keyboard(),
            ev=ev,
            event="master_list_clients.edit_failed",
        )
        return

    total_pages = max(1, ceil(len(all_clients) / TEXT_LIST_PAGE_SIZE))
    await _edit_select_page(
        callback.message,
        all_clients=all_clients,
        page=max(1, min(page, total_pages)),
        chunk=int(chunk),
        total_pages=total_pages,
    )


@router.callback_query(F.data.startswith(f"{CLIENTS_CB_PREFIX}s:back:"))
async def master_clients_select_back(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_list_clients", step="select_back")
    await callback.answer()
    if callback.message is None:
        return
    page = _parse_int_suffix(f"{CLIENTS_CB_PREFIX}s:back:", callback.data)
    if page is None:
        return

    telegram_id = callback.from_user.id
    master = await _fetch_master_or_alert(callback, telegram_id=telegram_id)
    if master is None:
        await safe_edit_text(
            callback.message,
            text=txt.no_clients_now(),
            reply_markup=_build_empty_clients_keyboard(),
            ev=ev,
            event="master_list_clients.edit_failed",
        )
        return
    all_clients = master.clients
    if not all_clients:
        await safe_edit_text(
            callback.message,
            text=txt.no_clients_now(),
            reply_markup=_build_empty_clients_keyboard(),
            ev=ev,
            event="master_list_clients.edit_failed",
        )
        return

    total_pages = max(1, ceil(len(all_clients) / TEXT_LIST_PAGE_SIZE))
    await _edit_list_page(callback.message, all_clients=all_clients, page=max(1, min(page, total_pages)))


@router.callback_query(F.data.startswith(f"{CLIENTS_CB_PREFIX}s:open:"))
async def master_clients_open_card(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_list_clients", step="open_card")
    await callback.answer()
    if callback.message is None:
        return
    parsed = _parse_open_action(callback.data)
    if parsed is None:
        return
    client_id, page, chunk = parsed

    telegram_id = callback.from_user.id
    master = await _fetch_master_or_alert(callback, telegram_id=telegram_id)
    if master is None:
        await safe_edit_text(
            callback.message,
            text=txt.no_clients_now(),
            reply_markup=_build_empty_clients_keyboard(),
            ev=ev,
            event="master_list_clients.edit_failed",
        )
        return
    all_clients = master.clients
    if not all_clients:
        await safe_edit_text(
            callback.message,
            text=txt.no_clients_now(),
            reply_markup=_build_empty_clients_keyboard(),
            ev=ev,
            event="master_list_clients.edit_failed",
        )
        return

    total_pages = max(1, ceil(len(all_clients) / TEXT_LIST_PAGE_SIZE))
    current_page = max(1, min(page, total_pages))
    ok = await _edit_client_card(
        callback.message,
        master=master,
        client_id=client_id,
        page=current_page,
        chunk=int(chunk),
    )
    if not ok:
        await callback.answer(common_txt.generic_error(), show_alert=True)


@router.callback_query(F.data.startswith(f"{CLIENTS_CARD_PREFIX}card:"))
async def master_clients_card_show(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_list_clients", step="card")
    await callback.answer()
    if callback.message is None:
        return
    parsed = _parse_card_action("card", callback.data)
    if parsed is None:
        return
    client_id, page, chunk = parsed

    telegram_id = callback.from_user.id
    master = await _fetch_master_or_alert(callback, telegram_id=telegram_id)
    if master is None or not master.clients:
        return

    total_pages = max(1, ceil(len(master.clients) / TEXT_LIST_PAGE_SIZE))
    current_page = max(1, min(page, total_pages))
    ok = await _edit_client_card(
        callback.message,
        master=master,
        client_id=client_id,
        page=current_page,
        chunk=int(chunk),
    )
    if not ok:
        await callback.answer(common_txt.generic_error(), show_alert=True)


def _attendance_badge(outcome: AttendanceOutcome) -> str:
    if outcome == AttendanceOutcome.ATTENDED:
        return "✅"
    if outcome == AttendanceOutcome.NO_SHOW:
        return "❌"
    return ""


@router.callback_query(F.data.startswith(f"{CLIENTS_CARD_PREFIX}history:"))
async def master_clients_card_history(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_list_clients", step="history")
    await callback.answer()
    if callback.message is None:
        return
    parsed = _parse_card_action("history", callback.data)
    if parsed is None:
        return
    client_id, page, chunk = parsed

    telegram_id = callback.from_user.id
    master = await _fetch_master_or_alert(callback, telegram_id=telegram_id)
    if master is None:
        return

    async with session_local() as session:
        stmt = (
            select(BookingEntity)
            .where(
                BookingEntity.master_id == int(master.id),
                BookingEntity.client_id == int(client_id),
            )
            .order_by(BookingEntity.start_at.desc())
            .limit(10)
        )
        bookings = list((await session.execute(stmt)).scalars().all())

    lines: list[str] = ["📅 История записей", ""]
    if not bookings:
        lines.append("Пока нет записей.")
    else:
        for booking in bookings:
            slot = to_zone(booking.start_at, master.timezone)
            lines.append(
                f"• {slot:%d.%m.%Y %H:%M} {status_badge(booking.status)}"
                f" {_attendance_badge(booking.attendance_outcome)}".rstrip(),
            )

    await safe_edit_text(
        callback.message,
        text="\n".join(lines).strip(),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="◀️ Назад",
                        callback_data=_card_action_cb(
                            "card",
                            client_id=int(client_id),
                            page=int(page),
                            chunk=int(chunk),
                        ),
                    ),
                ],
            ],
        ),
        ev=ev,
        event="master_list_clients.history_edit_failed",
    )


@router.callback_query(F.data.startswith(f"{CLIENTS_CARD_PREFIX}book:"))
async def master_clients_card_book(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_list_clients", step="book")
    await callback.answer()
    if callback.message is None:
        return
    parsed = _parse_card_action("book", callback.data)
    if parsed is None:
        return
    client_id, _page, _chunk = parsed

    telegram_id = callback.from_user.id
    master = await _fetch_master_or_alert(callback, telegram_id=telegram_id)
    if master is None:
        return

    client = next((c for c in master.clients if int(c.id) == int(client_id)), None)
    if client is None:
        await callback.answer(common_txt.generic_error(), show_alert=True)
        return

    from aiogram_calendar import SimpleCalendar

    from src.handlers.master.add_booking import AddBookingStates
    from src.texts import master_add_booking as add_booking_txt

    await state.clear()
    await state.update_data(
        master_id=int(master.id),
        master_slot_size=int(master.slot_size_min),
        master_timezone=str(master.timezone.value),
        master_day=None,
        client=client.to_state_dict(),
        confirm_in_progress=False,
    )

    reply_markup = await SimpleCalendar().start_calendar()
    await safe_edit_text(
        callback.message,
        text=add_booking_txt.choose_date(),
        reply_markup=reply_markup,
        ev=ev,
        event="master_list_clients.book_edit_failed",
    )
    await state.set_state(AddBookingStates.selecting_date)
