from collections.abc import Sequence
from html import escape as html_escape
from math import ceil

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from src.core.sa import active_session
from src.handlers.shared.ui import safe_delete, safe_edit_reply_markup, safe_edit_text
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.repositories import MasterNotFound, MasterRepository
from src.texts import common as common_txt, master_list_clients as txt
from src.texts.buttons import btn_back

ev = EventLogger(__name__)
router = Router(name=__name__)

PAGE_SIZE = 10
CLIENTS_PAGE_PREFIX = "master_clients_page:"

CLIENTS_CB_PREFIX = "m:cl:"


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
    raw = (data or "")
    if not raw.startswith(prefix):
        return None
    try:
        return int(raw[len(prefix) :])
    except ValueError:
        return None


def _parse_open_action(data: str | None) -> tuple[int, int] | None:
    parts = (data or "").split(":")
    # m:cl:s:open:<client_id>:p:<page>
    if len(parts) != 7:  # noqa: PLR2004
        return None
    if parts[:4] != ["m", "cl", "s", "open"] or parts[5] != "p":  # noqa: PLR2004
        return None
    try:
        client_id = int(parts[4])
        page = int(parts[6])
    except ValueError:
        return None
    return client_id, page


async def _fetch_master_clients(telegram_id: int) -> Sequence:
    async with active_session(begin=False) as session:
        repo = MasterRepository(session)
        try:
            master = await repo.get_with_clients_by_telegram_id(telegram_id)
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
    phone_part = f"{txt.phone_sep()}{html_escape(str(phone))}" if phone else ""
    offline_badge = common_txt.label_offline_badge() if getattr(client, "telegram_id", None) is None else ""
    return f"{name}{phone_part}{offline_badge}".strip()


def _placeholder_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text=txt.btn_placeholder(), callback_data="m:noop")


def _nav_button(*, direction: str, mode: str, page: int, total_pages: int) -> InlineKeyboardButton:
    if direction == "prev":
        if page <= 1:
            return _placeholder_button()
        return InlineKeyboardButton(text="⬅️", callback_data=f"{CLIENTS_CB_PREFIX}{mode}:p:{page - 1}")
    if direction == "next":
        if page >= total_pages:
            return _placeholder_button()
        return InlineKeyboardButton(text="➡️", callback_data=f"{CLIENTS_CB_PREFIX}{mode}:p:{page + 1}")
    raise ValueError("Unsupported direction.")


def _build_list_menu_keyboard(*, page: int, total_pages: int) -> InlineKeyboardMarkup:
    row = [
        _nav_button(direction="prev", mode="l", page=page, total_pages=total_pages),
        InlineKeyboardButton(text=txt.btn_find(), callback_data="m:clients:search"),
        InlineKeyboardButton(text=txt.btn_select(), callback_data=f"{CLIENTS_CB_PREFIX}l:select:{page}"),
        InlineKeyboardButton(text=txt.btn_add(), callback_data="m:clients:add"),
        _nav_button(direction="next", mode="l", page=page, total_pages=total_pages),
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            row,
            [InlineKeyboardButton(text=btn_back(), callback_data=f"{CLIENTS_CB_PREFIX}l:menu")],
        ],
    )


def _build_select_menu_keyboard(*, page: int, total_pages: int) -> list[list[InlineKeyboardButton]]:
    row = [
        _nav_button(direction="prev", mode="s", page=page, total_pages=total_pages),
        InlineKeyboardButton(text=txt.btn_find(), callback_data="m:clients:search"),
        _nav_button(direction="next", mode="s", page=page, total_pages=total_pages),
    ]
    return [
        row,
        [InlineKeyboardButton(text=txt.btn_back_to_list(), callback_data=f"{CLIENTS_CB_PREFIX}s:back:{page}")],
    ]


def _build_select_keyboard(
    all_clients: Sequence,
    *,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    clients_page = all_clients[start:end]

    rows: list[list[InlineKeyboardButton]] = []
    for client in clients_page:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_render_client_label(client),
                    callback_data=f"{CLIENTS_CB_PREFIX}s:open:{int(client.id)}:p:{page}",
                ),
            ],
        )
    rows.extend(_build_select_menu_keyboard(page=page, total_pages=total_pages))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_page_payload(
    all_clients: Sequence,
    *,
    page: int,
) -> tuple[str, InlineKeyboardMarkup, int] | None:
    total_pages = max(1, ceil(len(all_clients) / PAGE_SIZE))
    if page < 1 or page > total_pages:
        ev.debug(
            "master_list_clients.callback_invalid",
            reason="page_out_of_range",
            page=page,
            total_pages=total_pages,
        )
        return None

    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
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


async def _fetch_clients_or_alert(callback: CallbackQuery, *, telegram_id: int) -> Sequence | None:
    try:
        return await _fetch_master_clients(telegram_id)
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
    ]

    for offset, client in enumerate(clients, start=1):
        lines.append(_render_client_line(client, index=start_index + offset))

    lines.extend(["", txt.offline_legend()])
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
        ],
    )


async def start_clients_entry(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_list_clients", step="start")
    telegram_id = callback.from_user.id
    all_clients = await _fetch_clients_or_alert(callback, telegram_id=telegram_id)
    if all_clients is None:
        return

    if not all_clients:
        await callback.message.answer(
            text=txt.empty_long(),
            reply_markup=_build_empty_clients_keyboard(),
        )
        return

    page = 1
    total_pages = max(1, ceil(len(all_clients) / PAGE_SIZE))

    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    clients_page = all_clients[start:end]

    text = _build_clients_page_text(clients_page, page, total_pages, start_index=start)
    keyboard = _build_list_menu_keyboard(page=page, total_pages=total_pages)

    await callback.message.answer(text=text, reply_markup=keyboard)


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
    all_clients = await _fetch_clients_or_alert(callback, telegram_id=telegram_id)
    if all_clients is None:
        return

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


async def _edit_select_page(message, *, all_clients: Sequence, page: int, total_pages: int) -> None:
    await safe_edit_text(
        message,
        text=f"{txt.choose_title(page=page, total_pages=total_pages)}\n\n{txt.offline_legend()}",
        reply_markup=_build_select_keyboard(all_clients, page=page, total_pages=total_pages),
        ev=ev,
        event="master_list_clients.select_edit_failed",
    )


async def _edit_client_card(message, *, all_clients: Sequence, client_id: int, page: int) -> bool:
    client = next((c for c in all_clients if int(c.id) == int(client_id)), None)
    if client is None:
        return False

    from src.texts import edit_client as edit_client_txt

    await safe_edit_text(
        message,
        text=edit_client_txt.client_card(
            name=getattr(client, "name", None),
            phone=getattr(client, "phone", None),
            is_offline=getattr(client, "telegram_id", None) is None,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=txt.btn_back_to_choose(),
                        callback_data=f"{CLIENTS_CB_PREFIX}s:p:{page}",
                    ),
                ],
            ],
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
    all_clients = await _fetch_clients_or_alert(callback, telegram_id=telegram_id)
    if not all_clients:
        await safe_edit_text(
            callback.message,
            text=txt.no_clients_now(),
            reply_markup=_build_empty_clients_keyboard(),
            ev=ev,
            event="master_list_clients.edit_failed",
        )
        return

    total_pages = max(1, ceil(len(all_clients) / PAGE_SIZE))
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
    all_clients = await _fetch_clients_or_alert(callback, telegram_id=telegram_id)
    if not all_clients:
        await safe_edit_text(
            callback.message,
            text=txt.no_clients_now(),
            reply_markup=_build_empty_clients_keyboard(),
            ev=ev,
            event="master_list_clients.edit_failed",
        )
        return

    total_pages = max(1, ceil(len(all_clients) / PAGE_SIZE))
    await _edit_select_page(
        callback.message,
        all_clients=all_clients,
        page=max(1, min(page, total_pages)),
        total_pages=total_pages,
    )


@router.callback_query(F.data.startswith(f"{CLIENTS_CB_PREFIX}s:p:"))
async def master_clients_select_page(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_list_clients", step="select_page")
    await callback.answer()
    if callback.message is None:
        return
    page = _parse_int_suffix(f"{CLIENTS_CB_PREFIX}s:p:", callback.data)
    if page is None:
        return

    telegram_id = callback.from_user.id
    all_clients = await _fetch_clients_or_alert(callback, telegram_id=telegram_id)
    if not all_clients:
        await safe_edit_text(
            callback.message,
            text=txt.no_clients_now(),
            reply_markup=_build_empty_clients_keyboard(),
            ev=ev,
            event="master_list_clients.edit_failed",
        )
        return

    total_pages = max(1, ceil(len(all_clients) / PAGE_SIZE))
    await _edit_select_page(
        callback.message,
        all_clients=all_clients,
        page=max(1, min(page, total_pages)),
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
    all_clients = await _fetch_clients_or_alert(callback, telegram_id=telegram_id)
    if not all_clients:
        await safe_edit_text(
            callback.message,
            text=txt.no_clients_now(),
            reply_markup=_build_empty_clients_keyboard(),
            ev=ev,
            event="master_list_clients.edit_failed",
        )
        return

    total_pages = max(1, ceil(len(all_clients) / PAGE_SIZE))
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
    client_id, page = parsed

    telegram_id = callback.from_user.id
    all_clients = await _fetch_clients_or_alert(callback, telegram_id=telegram_id)
    if not all_clients:
        await safe_edit_text(
            callback.message,
            text=txt.no_clients_now(),
            reply_markup=_build_empty_clients_keyboard(),
            ev=ev,
            event="master_list_clients.edit_failed",
        )
        return

    total_pages = max(1, ceil(len(all_clients) / PAGE_SIZE))
    current_page = max(1, min(page, total_pages))
    ok = await _edit_client_card(callback.message, all_clients=all_clients, client_id=client_id, page=current_page)
    if not ok:
        await callback.answer(common_txt.generic_error(), show_alert=True)
