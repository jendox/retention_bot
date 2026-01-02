from __future__ import annotations

from html import escape as html_escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import session_local
from src.filters.user_role import UserRole
from src.handlers.shared.guards import rate_limit_callback, rate_limit_message
from src.handlers.shared.ui import safe_delete, safe_edit_text
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.rate_limiter import RateLimiter
from src.repositories import ClientNotFound, ClientRepository
from src.schemas.enums import Timezone
from src.texts import client_list_masters as txt
from src.texts.buttons import btn_back, btn_close
from src.texts.client_booking import booking_limit_reached
from src.texts.client_messages import CLIENT_NOT_FOUND_MESSAGE
from src.use_cases.entitlements import EntitlementsService
from src.user_context import ActiveRole
from src.utils import format_phone_display, format_phone_e164, format_work_days_label, track_message

router = Router(name=__name__)
ev = EventLogger(__name__)

CB_PREFIX = "c:masters:"
MAIN_KEY = "client_masters_main"
LIST_KEY = "client_masters_items"

TEXT_PAGE_SIZE = 10
SELECT_FIRST_SIZE = 6
CHUNK_1 = 1
CHUNK_2 = 2


def _main_ref(chat_id: int, message_id: int) -> dict[str, int]:
    return {"chat_id": int(chat_id), "message_id": int(message_id)}


async def _set_main_ref(state: FSMContext, *, chat_id: int, message_id: int) -> None:
    await state.update_data(**{MAIN_KEY: _main_ref(chat_id, message_id)})


def _get_main_ref(data: dict, *, telegram_id: int) -> tuple[int, int] | None:
    ref = data.get(MAIN_KEY) or {}
    chat_id = ref.get("chat_id") or telegram_id
    message_id = ref.get("message_id")
    if message_id is None:
        return None
    return int(chat_id), int(message_id)


def _total_pages(total: int) -> int:
    return max(1, (total + TEXT_PAGE_SIZE - 1) // TEXT_PAGE_SIZE)


def _clamp_page(page: int, total_pages: int) -> int:
    return max(1, min(int(page), int(total_pages)))


def _master_line(master: dict) -> str:
    name = html_escape(str(master.get("name") or "Мастер"))
    phone = format_phone_e164(str(master.get("phone") or ""))
    phone_safe = html_escape(phone) if phone else "—"
    return f"• <b>{name}</b> · {phone_safe}"


def _render_list_page(*, masters: list[dict], page: int) -> str:
    total_pages = _total_pages(len(masters))
    page = _clamp_page(page, total_pages)
    start = (page - 1) * TEXT_PAGE_SIZE
    end = start + TEXT_PAGE_SIZE
    items = masters[start:end]
    lines = [txt.title_page(page=page, total_pages=total_pages), ""]
    lines.extend(_master_line(m) for m in items)
    return "\n".join(lines).strip()


def _render_select_page(*, masters: list[dict], page: int, chunk: int) -> str:
    total_pages = _total_pages(len(masters))
    page = _clamp_page(page, total_pages)
    return txt.choose_title(page=page, total_pages=total_pages)


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


def _select_slice(*, masters: list[dict], page: int, chunk: int) -> tuple[list[dict], bool]:
    total_pages = _total_pages(len(masters))
    page = _clamp_page(page, total_pages)
    base_start = (page - 1) * TEXT_PAGE_SIZE
    base_end = base_start + TEXT_PAGE_SIZE
    base = masters[base_start:base_end]

    if chunk == CHUNK_2 and len(base) > SELECT_FIRST_SIZE:
        return base[SELECT_FIRST_SIZE:], False
    visible = base[:SELECT_FIRST_SIZE]
    has_more = len(base) > SELECT_FIRST_SIZE
    return visible, has_more


def _kb_select(*, masters: list[dict], page: int, chunk: int) -> InlineKeyboardMarkup:
    total_pages = _total_pages(len(masters))
    page = _clamp_page(page, total_pages)
    visible, has_more = _select_slice(masters=masters, page=page, chunk=chunk)

    rows: list[list[InlineKeyboardButton]] = []
    rows.extend(_kb_select_master_rows(visible=visible, page=page, chunk=chunk))
    nav_row = _kb_select_nav_row(total_pages=total_pages, page=page)
    if nav_row is not None:
        rows.append(nav_row)
    toggle_row = _kb_select_chunk_toggle_row(has_more=has_more, page=page, chunk=chunk)
    if toggle_row is not None:
        rows.append(toggle_row)
    rows.append([InlineKeyboardButton(text=txt.btn_back_to_list(), callback_data=f"{CB_PREFIX}l:p:{page}")])
    rows.append([InlineKeyboardButton(text=btn_close(), callback_data=f"{CB_PREFIX}close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_select_master_rows(*, visible: list[dict], page: int, chunk: int) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    for m in visible:
        label = str(m.get("name") or "Мастер")
        phone = format_phone_display(str(m.get("phone") or ""))
        if phone:
            label = f"{label} · {phone}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{CB_PREFIX}open:{int(m['id'])}:p:{page}:c:{chunk}",
                ),
            ],
        )
    return rows


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


def _kb_master_card(*, master_id: int, master_tg: int, page: int, chunk: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=txt.btn_book(), callback_data=f"{CB_PREFIX}book:{int(master_id)}"),
                InlineKeyboardButton(text=txt.btn_write_master(), url=f"tg://user?id={int(master_tg)}"),
            ],
            [InlineKeyboardButton(text=btn_back(), callback_data=f"{CB_PREFIX}s:p:{int(page)}:c:{int(chunk)}")],
        ],
    )


def _render_master_card(master: dict) -> str:
    name = html_escape(str(master.get("name") or "Мастер"))
    phone = format_phone_e164(str(master.get("phone") or ""))
    phone_safe = html_escape(phone) if phone else "—"

    days = format_work_days_label(list(master.get("work_days") or [])).strip()
    days_display = days if days else "—"

    start_time = str(master.get("start_time") or "").strip()
    end_time = str(master.get("end_time") or "").strip()
    time_display = f"{start_time}–{end_time}".strip("–").strip() if (start_time and end_time) else "—"

    return (
        f"<b>{name}</b>\n\n"
        f"📞 {phone_safe}\n\n"
        f"📆 Рабочие дни: {html_escape(days_display)}\n"
        f"🕒 Время работы: {html_escape(time_display)}"
    )


async def _load_masters(telegram_id: int) -> list[dict] | None:
    async with session_local() as session:
        repo = ClientRepository(session)
        try:
            client = await repo.get_details_by_telegram_id(telegram_id)
        except ClientNotFound:
            return None
    masters = client.masters
    return [m.to_state_dict() for m in masters]


async def start_client_list_masters(
    message: Message,
    state: FSMContext,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="client_list_masters", step="start")
    if not await rate_limit_message(message, rate_limiter, name="client_list_masters:start", ttl_sec=2):
        return

    telegram_id = message.from_user.id
    ev.info("client_list_masters.start")
    masters = await _load_masters(telegram_id)
    if masters is None:
        ev.warning("client_list_masters.client_not_found")
        await message.answer(CLIENT_NOT_FOUND_MESSAGE)
        return

    if not masters:
        ev.info("client_list_masters.start_result", outcome="empty")
        msg = await message.answer(
            text=txt.empty(),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=btn_close(), callback_data=f"{CB_PREFIX}close")]],
            ),
        )
        await _set_main_ref(state, chat_id=msg.chat.id, message_id=msg.message_id)
        await safe_delete(message, ev=ev, event="client_list_masters.delete_menu_message_failed")
        return

    await state.update_data(**{LIST_KEY: masters})
    total_pages = _total_pages(len(masters))
    page = 1
    ev.info("client_list_masters.start_result", outcome="listed", masters_count=len(masters))
    msg = await message.answer(
        text=_render_list_page(masters=masters, page=page),
        reply_markup=_kb_list(total_pages=total_pages, page=page),
        parse_mode="HTML",
    )
    await _set_main_ref(state, chat_id=msg.chat.id, message_id=msg.message_id)
    await safe_delete(message, ev=ev, event="client_list_masters.delete_menu_message_failed")


@router.callback_query(UserRole(ActiveRole.CLIENT), F.data == f"{CB_PREFIX}noop")
async def noop(callback: CallbackQuery) -> None:
    bind_log_context(flow="client_list_masters", step="noop")
    await callback.answer()


@router.callback_query(UserRole(ActiveRole.CLIENT), F.data == f"{CB_PREFIX}close")
async def close(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="client_list_masters", step="close")
    ev.info("client_list_masters.close")
    await callback.answer()
    if callback.message is not None:
        await safe_delete(callback.message, ev=ev, event="client_list_masters.delete_failed")
    await state.clear()


@router.callback_query(UserRole(ActiveRole.CLIENT), F.data.startswith(f"{CB_PREFIX}l:p:"))
async def list_page(callback: CallbackQuery, state: FSMContext, rate_limiter: RateLimiter | None = None) -> None:
    bind_log_context(flow="client_list_masters", step="list_page")
    if not await rate_limit_callback(callback, rate_limiter, name="client_list_masters:list_page", ttl_sec=1):
        return
    await callback.answer()
    if callback.message is None:
        return

    try:
        page = int((callback.data or "").split(":")[-1])
    except ValueError:
        return

    data = await state.get_data()
    masters: list[dict] = data.get(LIST_KEY) or []
    total_pages = _total_pages(len(masters))
    page = _clamp_page(page, total_pages)
    await safe_edit_text(
        callback.message,
        text=_render_list_page(masters=masters, page=page),
        reply_markup=_kb_list(total_pages=total_pages, page=page),
        parse_mode="HTML",
        ev=ev,
        event="client_list_masters.edit_list_failed",
    )


@router.callback_query(UserRole(ActiveRole.CLIENT), F.data.startswith(f"{CB_PREFIX}s:p:"))
async def select_page(callback: CallbackQuery, state: FSMContext, rate_limiter: RateLimiter | None = None) -> None:
    bind_log_context(flow="client_list_masters", step="select_page")
    if not await rate_limit_callback(callback, rate_limiter, name="client_list_masters:select_page", ttl_sec=1):
        return
    await callback.answer()
    if callback.message is None:
        return

    # c:masters:s:p:<page>:c:<chunk>
    parts = (callback.data or "").split(":")
    try:
        page = int(parts[4])
        chunk = int(parts[6])
    except Exception:
        return

    data = await state.get_data()
    masters: list[dict] = data.get(LIST_KEY) or []
    total_pages = _total_pages(len(masters))
    page = _clamp_page(page, total_pages)
    chunk = CHUNK_2 if chunk == CHUNK_2 else CHUNK_1
    await safe_edit_text(
        callback.message,
        text=_render_select_page(masters=masters, page=page, chunk=chunk),
        reply_markup=_kb_select(masters=masters, page=page, chunk=chunk),
        parse_mode="HTML",
        ev=ev,
        event="client_list_masters.edit_select_failed",
    )


@router.callback_query(UserRole(ActiveRole.CLIENT), F.data.startswith(f"{CB_PREFIX}open:"))
async def open_master(callback: CallbackQuery, state: FSMContext, rate_limiter: RateLimiter | None = None) -> None:
    bind_log_context(flow="client_list_masters", step="open")
    if not await rate_limit_callback(callback, rate_limiter, name="client_list_masters:open", ttl_sec=1):
        return
    await callback.answer()
    if callback.message is None:
        return

    # c:masters:open:<id>:p:<page>:c:<chunk>
    parts = (callback.data or "").split(":")
    try:
        master_id = int(parts[3])
        page = int(parts[5])
        chunk = int(parts[7])
    except Exception:
        return
    ev.info("client_list_masters.open", master_id=int(master_id))

    data = await state.get_data()
    masters: list[dict] = data.get(LIST_KEY) or []
    master = next((m for m in masters if int(m.get("id")) == int(master_id)), None)
    if master is None:
        return

    master_tg = int(master.get("telegram_id") or 0)
    if master_tg <= 0:
        return

    await safe_edit_text(
        callback.message,
        text=_render_master_card(master),
        reply_markup=_kb_master_card(master_id=master_id, master_tg=master_tg, page=page, chunk=chunk),
        parse_mode="HTML",
        ev=ev,
        event="client_list_masters.edit_card_failed",
    )


@router.callback_query(UserRole(ActiveRole.CLIENT), F.data.startswith(f"{CB_PREFIX}book:"))
async def book_from_card(callback: CallbackQuery, state: FSMContext, rate_limiter: RateLimiter | None = None) -> None:
    bind_log_context(flow="client_list_masters", step="book")
    if not await rate_limit_callback(callback, rate_limiter, name="client_list_masters:book", ttl_sec=2):
        return
    await callback.answer()
    if callback.message is None:
        return

    try:
        master_id = int((callback.data or "").split(":")[-1])
    except ValueError:
        return

    telegram_id = callback.from_user.id
    async with session_local() as session:
        repo = ClientRepository(session)
        try:
            client = await repo.get_details_by_telegram_id(telegram_id)
        except ClientNotFound:
            await callback.message.answer(CLIENT_NOT_FOUND_MESSAGE)
            await state.clear()
            return

        entitlements = EntitlementsService(session)
        check = await entitlements.can_create_booking(master_id=master_id)
        if not check.allowed:
            await state.clear()
            await safe_edit_text(
                callback.message,
                text=booking_limit_reached(),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text=btn_close(), callback_data=f"{CB_PREFIX}close")]],
                ),
                parse_mode="HTML",
                ev=ev,
                event="client_list_masters.edit_booking_limit_failed",
            )
            return

    # Start booking in-place (edit this message into the calendar).
    await state.clear()
    await state.update_data(
        client_id=client.id,
        client_timezone=Timezone(str(client.timezone.value)),
        client_name=client.name,
        master_id=int(master_id),
    )
    from src.handlers.client import booking as booking_h

    reply_markup = await booking_h._calendar_markup(state)
    await safe_edit_text(
        callback.message,
        text=booking_h._calendar_prompt_text(),
        reply_markup=reply_markup,
        parse_mode="HTML",
        ev=ev,
        event="client_list_masters.edit_booking_calendar_failed",
    )
    from src.handlers.client.booking import ClientBooking

    await track_message(state, callback.message, bucket="client_booking")
    await state.set_state(ClientBooking.selecting_date)
