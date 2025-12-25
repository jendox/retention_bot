from collections.abc import Sequence
from html import escape as html_escape
from math import ceil

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from src.core.sa import active_session
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.repositories import MasterNotFound, MasterRepository
from src.texts import common as common_txt, master_list_clients as txt
from src.texts.buttons import btn_back

ev = EventLogger(__name__)
router = Router(name=__name__)

PAGE_SIZE = 10
CLIENTS_PAGE_PREFIX = "master_clients_page:"
BACK_TO_CLIENTS_MENU_CB = "m:clients:back"


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
    keyboard = _build_clients_pagination_keyboard(page, total_pages)
    return text, keyboard, total_pages


async def _safe_edit_text(message, *, text: str, reply_markup: InlineKeyboardMarkup) -> None:
    try:
        await message.edit_text(text=text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            ev.debug("master_list_clients.not_modified")
            return
        ev.warning("master_list_clients.edit_failed", error=str(exc))


async def _close_clients_list(message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest as exc:
        # Best-effort cleanup: deletion is racy; hide keyboard if needed.
        ev.debug("master_list_clients.close_delete_failed", error=str(exc))
        try:
            await message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest as exc2:
            ev.debug("master_list_clients.close_disable_keyboard_failed", error=str(exc2))


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
            [InlineKeyboardButton(text=btn_back(), callback_data=BACK_TO_CLIENTS_MENU_CB)],
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
    keyboard = _build_clients_pagination_keyboard(page, total_pages)

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
        await _safe_edit_text(
            callback.message,
            text=txt.no_clients_now(),
            reply_markup=_build_empty_clients_keyboard(),
        )
        return

    payload = _build_page_payload(all_clients, page=page)
    if payload is None:
        return
    text, keyboard, _ = payload
    await _safe_edit_text(callback.message, text=text, reply_markup=keyboard)
