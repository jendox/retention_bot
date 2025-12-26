import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from src.core.sa import active_session, session_local
from src.datetime_utils import to_zone
from src.filters.user_role import UserRole
from src.handlers.shared.guards import rate_limit_callback, rate_limit_message
from src.handlers.shared.ui import safe_bot_edit_message_text, safe_edit_text
from src.models import Booking as BookingEntity
from src.observability.context import bind_log_context
from src.rate_limiter import RateLimiter
from src.repositories import ClientNotFound, ClientRepository, MasterNotFound, MasterRepository
from src.schemas import ClientUpdate
from src.schemas.enums import AttendanceOutcome, status_badge
from src.texts import common as common_txt, edit_client as txt
from src.texts.buttons import btn_back, btn_cancel
from src.texts.master_client_card import ClientHints, ClientSummary, card as render_client_view
from src.user_context import ActiveRole
from src.utils import answer_tracked, cleanup_messages, format_phone_display, track_message, validate_phone

logger = logging.getLogger(__name__)
router = Router(name=__name__)

EDIT_CLIENT_BUCKET = "master_edit_client"
EDIT_CLIENT_CARD_BUCKET = "master_edit_client_card"
_GC_BUCKETS_KEY = "_gc_buckets"


class EditClientStates(StatesGroup):
    query = State()
    choosing = State()
    action = State()
    edit_name = State()
    edit_phone = State()


def _kb_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=btn_cancel(), callback_data="m:edit_client:cancel")]],
    )


def _kb_results(clients: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for raw in clients[:10]:
        label = raw.get("name") or common_txt.label_default_client()
        phone = raw.get("phone")
        if phone:
            label += f" ({phone})"
        if raw.get("telegram_id") is None:
            label += common_txt.label_offline_badge()
        rows.append([InlineKeyboardButton(text=label, callback_data=f"m:edit_client:pick:{raw['id']}")])
    rows.append([InlineKeyboardButton(text=btn_back(), callback_data="m:edit_client:back")])
    rows.append([InlineKeyboardButton(text=btn_cancel(), callback_data="m:edit_client:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_actions(*, can_edit_phone: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=txt.btn_edit_name(), callback_data="m:edit_client:edit_name")],
    ]
    if can_edit_phone:
        rows.append([InlineKeyboardButton(text=txt.btn_edit_phone(), callback_data="m:edit_client:edit_phone")])
    rows.append([InlineKeyboardButton(text=txt.btn_back_to_search(), callback_data="m:edit_client:back")])
    rows.append([InlineKeyboardButton(text=btn_cancel(), callback_data="m:edit_client:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_client_view(*, client_id: int, telegram_id: int | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if telegram_id is not None:
        rows.append([InlineKeyboardButton(text="💬 Написать в Telegram", url=f"tg://user?id={int(telegram_id)}")])
    rows.extend(
        [
            [
                InlineKeyboardButton(text="➕ Записать клиента", callback_data=f"m:edit_client:book:{int(client_id)}"),
                InlineKeyboardButton(
                    text="📅 История записей",
                    callback_data=f"m:edit_client:history:{int(client_id)}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Редактировать клиента",
                    callback_data=f"m:edit_client:edit_menu:{int(client_id)}",
                ),
            ],
            [InlineKeyboardButton(text=txt.btn_back_to_search(), callback_data="m:edit_client:back")],
            [InlineKeyboardButton(text=btn_cancel(), callback_data="m:edit_client:cancel")],
        ],
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _fetch_client_stats(*, master_id: int, client_id: int) -> tuple[datetime | None, int, int]:
    stmt = select(
        func.max(BookingEntity.start_at).filter(BookingEntity.attendance_outcome == AttendanceOutcome.ATTENDED),
        func.count().filter(BookingEntity.attendance_outcome == AttendanceOutcome.ATTENDED),
        func.count().filter(BookingEntity.attendance_outcome == AttendanceOutcome.NO_SHOW),
    ).where(
        BookingEntity.master_id == master_id,
        BookingEntity.client_id == client_id,
    )
    async with session_local() as session:
        row = (await session.execute(stmt)).one()
    last_visit_at, visits_count, no_show_count = row
    return last_visit_at, int(visits_count or 0), int(no_show_count or 0)


async def _show_client_view_message(
    message: Message,
    *,
    telegram_id: int,
    client_id: int,
) -> bool:
    async with session_local() as session:
        master_repo = MasterRepository(session)
        try:
            master = await master_repo.get_with_clients_by_telegram_id(telegram_id)
        except MasterNotFound:
            return False

    client = next((c for c in master.clients if int(c.id) == int(client_id)), None)
    if client is None:
        return False

    last_visit_at, visits_count, no_show_count = await _fetch_client_stats(
        master_id=int(master.id),
        client_id=int(client_id),
    )
    last_visit_day = to_zone(last_visit_at, master.timezone).date() if last_visit_at is not None else None

    name_safe = common_txt.label_default_client() if not getattr(client, "name", None) else str(client.name)
    phone_display = format_phone_display(str(client.phone)) if getattr(client, "phone", None) else None

    text = render_client_view(
        name=name_safe,
        is_offline=client.telegram_id is None,
        phone=phone_display,
        summary=ClientSummary(
            last_visit_day=last_visit_day,
            total_visits=int(visits_count),
            no_show=int(no_show_count),
        ),
        hints=ClientHints(show_offline_hint=True, show_noshow_hint=True),
    )
    return await safe_edit_text(
        message,
        text=text,
        reply_markup=_kb_client_view(client_id=int(client_id), telegram_id=client.telegram_id),
        parse_mode="HTML",
        ev=None,
        event="edit_client.view_failed",
    )


def _render_client_card(client: dict) -> str:
    return txt.client_card(
        name=client.get("name"),
        phone=client.get("phone"),
        is_offline=client.get("telegram_id") is None,
    )


async def _move_message_to_bucket(
    state: FSMContext,
    message: Message,
    *,
    src_bucket: str,
    dst_bucket: str,
) -> None:
    data = await state.get_data()
    buckets: dict = data.get(_GC_BUCKETS_KEY, {})

    src_data = buckets.get(src_bucket, {})
    src_ids: list[int] = list(src_data.get("message_ids", []))
    if message.message_id in src_ids:
        src_ids = [mid for mid in src_ids if mid != message.message_id]
        src_data["message_ids"] = src_ids
        buckets[src_bucket] = src_data

    dst_data = buckets.get(dst_bucket, {})
    dst_ids: list[int] = list(dst_data.get("message_ids", []))
    if message.message_id not in dst_ids:
        dst_ids.append(message.message_id)
    dst_data["message_ids"] = dst_ids
    dst_data["chat_id"] = message.chat.id
    buckets[dst_bucket] = dst_data

    await state.update_data(**{_GC_BUCKETS_KEY: buckets})


async def _get_last_card_message_ref(state: FSMContext) -> tuple[int | None, int | None]:
    data = await state.get_data()
    buckets: dict = data.get(_GC_BUCKETS_KEY, {})
    card_bucket = buckets.get(EDIT_CLIENT_CARD_BUCKET, {})
    chat_id = card_bucket.get("chat_id")
    message_ids: list[int] = card_bucket.get("message_ids", [])
    message_id = message_ids[-1] if message_ids else None
    return chat_id, message_id


async def _update_card(message: Message, state: FSMContext, selected: dict) -> None:
    chat_id, message_id = await _get_last_card_message_ref(state)
    if chat_id is not None and message_id is not None:
        ok = await safe_bot_edit_message_text(
            message.bot,
            chat_id=int(chat_id),
            message_id=int(message_id),
            text=_render_client_card(selected),
            reply_markup=_kb_actions(),
            event="edit_client.update_card_failed",
        )
        if ok:
            return

    card = await message.answer(_render_client_card(selected), reply_markup=_kb_actions(), parse_mode="HTML")
    await track_message(state, card, bucket=EDIT_CLIENT_CARD_BUCKET)


async def start_edit_client(
    callback: CallbackQuery,
    state: FSMContext,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="master_edit_client", step="start")
    if not await rate_limit_callback(callback, rate_limiter, name="master_edit_client:start", ttl_sec=2):
        return
    await callback.answer()
    await cleanup_messages(state, callback.bot, bucket=EDIT_CLIENT_BUCKET)
    await cleanup_messages(state, callback.bot, bucket=EDIT_CLIENT_CARD_BUCKET)
    await state.clear()
    await answer_tracked(
        callback.message,
        state,
        text=txt.ask_name_or_phone(),
        bucket=EDIT_CLIENT_BUCKET,
        reply_markup=_kb_cancel(),
    )
    await state.set_state(EditClientStates.query)


@router.message(UserRole(ActiveRole.MASTER), StateFilter(EditClientStates.query))
async def process_query(message: Message, state: FSMContext, rate_limiter: RateLimiter | None = None) -> None:
    bind_log_context(flow="master_edit_client", step="query")
    if not await rate_limit_message(message, rate_limiter, name="master_edit_client:query", ttl_sec=1):
        return
    await track_message(state, message, bucket=EDIT_CLIENT_BUCKET)
    query = (message.text or "").strip()
    if not query:
        await answer_tracked(
            message,
            state,
            text=txt.name_or_phone_required(),
            bucket=EDIT_CLIENT_BUCKET,
            reply_markup=_kb_cancel(),
        )
        return

    telegram_id = message.from_user.id
    async with session_local() as session:
        master_repo = MasterRepository(session)
        try:
            master = await master_repo.get_with_clients_by_telegram_id(telegram_id)
        except MasterNotFound:
            await message.answer(txt.master_profile_not_found())
            return

    q = query.lower()
    matches = [
        client.to_state_dict()
        for client in master.clients
        if q in (client.name or "").lower() or q in (client.phone or "")
    ]
    if not matches:
        await answer_tracked(
            message,
            state,
            text=txt.no_clients_found(),
            bucket=EDIT_CLIENT_BUCKET,
            reply_markup=_kb_cancel(),
        )
        return

    await state.update_data(edit_client_results=matches)
    await answer_tracked(
        message,
        state,
        text=txt.choose_client(),
        bucket=EDIT_CLIENT_BUCKET,
        reply_markup=_kb_results(matches),
    )
    await state.set_state(EditClientStates.choosing)


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(
        EditClientStates.query,
        EditClientStates.choosing,
        EditClientStates.action,
        EditClientStates.edit_name,
        EditClientStates.edit_phone,
    ),
    F.data == "m:edit_client:cancel",
)
async def cancel(
    callback: CallbackQuery,
    state: FSMContext,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="master_edit_client", step="cancel")
    if not await rate_limit_callback(callback, rate_limiter, name="master_edit_client:cancel", ttl_sec=1):
        return
    await callback.answer(common_txt.cancelled(), show_alert=True)
    await cleanup_messages(state, callback.bot, bucket=EDIT_CLIENT_BUCKET)
    await cleanup_messages(state, callback.bot, bucket=EDIT_CLIENT_CARD_BUCKET)
    await state.clear()


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(
        EditClientStates.query,
        EditClientStates.choosing,
        EditClientStates.action,
        EditClientStates.edit_name,
        EditClientStates.edit_phone,
    ),
    F.data == "m:edit_client:back",
)
async def back(
    callback: CallbackQuery,
    state: FSMContext,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="master_edit_client", step="back")
    if not await rate_limit_callback(callback, rate_limiter, name="master_edit_client:back", ttl_sec=1):
        return
    await callback.answer()
    await cleanup_messages(state, callback.bot, bucket=EDIT_CLIENT_BUCKET)
    await cleanup_messages(state, callback.bot, bucket=EDIT_CLIENT_CARD_BUCKET)
    await state.clear()
    await answer_tracked(
        callback.message,
        state,
        text=txt.ask_name_or_phone(),
        bucket=EDIT_CLIENT_BUCKET,
        reply_markup=_kb_cancel(),
    )
    await state.set_state(EditClientStates.query)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith("m:edit_client:open:"))
async def open_client_direct(
    callback: CallbackQuery,
    state: FSMContext,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="master_edit_client", step="open_direct")
    if not await rate_limit_callback(callback, rate_limiter, name="master_edit_client:open_direct", ttl_sec=1):
        return
    await callback.answer()
    if callback.message is None:
        return

    raw_id = (callback.data or "").removeprefix("m:edit_client:open:")
    try:
        client_id = int(raw_id)
    except ValueError:
        await callback.answer(txt.invalid_client(), show_alert=True)
        return

    await cleanup_messages(state, callback.bot, bucket=EDIT_CLIENT_BUCKET)
    await cleanup_messages(state, callback.bot, bucket=EDIT_CLIENT_CARD_BUCKET)
    await state.clear()

    telegram_id = callback.from_user.id
    async with session_local() as session:
        master_repo = MasterRepository(session)
        try:
            master = await master_repo.get_with_clients_by_telegram_id(telegram_id)
        except MasterNotFound:
            await callback.answer(txt.master_profile_not_found(), show_alert=True)
            return

    selected = next((c.to_state_dict() for c in master.clients if int(c.id) == client_id), None)
    if selected is None:
        await callback.answer(txt.invalid_client(), show_alert=True)
        return

    await state.update_data(edit_client_results=[selected], edit_client_selected=selected)
    await safe_edit_text(
        callback.message,
        text=_render_client_card(selected),
        reply_markup=_kb_actions(),
        parse_mode="HTML",
        event="edit_client.open_direct_failed",
    )
    await state.set_state(EditClientStates.action)


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(EditClientStates.choosing),
    F.data.startswith("m:edit_client:pick:"),
)
async def pick_client(
    callback: CallbackQuery,
    state: FSMContext,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="master_edit_client", step="pick")
    if not await rate_limit_callback(callback, rate_limiter, name="master_edit_client:pick", ttl_sec=1):
        return
    await callback.answer()
    if callback.message:
        await _move_message_to_bucket(
            state,
            callback.message,
            src_bucket=EDIT_CLIENT_BUCKET,
            dst_bucket=EDIT_CLIENT_CARD_BUCKET,
        )
    try:
        client_id = int((callback.data or "").split(":")[-1])
    except ValueError:
        await callback.answer(txt.invalid_client(), show_alert=True)
        return

    data = await state.get_data()
    results: list[dict] = data.get("edit_client_results", [])
    selected = next((c for c in results if c.get("id") == client_id), None)
    if selected is None:
        await callback.answer(txt.client_not_found_in_results(), show_alert=True)
        return

    await state.update_data(edit_client_selected=selected)
    if callback.message:
        await _show_client_view_message(
            callback.message,
            telegram_id=callback.from_user.id,
            client_id=int(client_id),
        )
    await state.set_state(EditClientStates.action)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith("m:edit_client:view:"))
async def view_client(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_edit_client", step="view")
    await callback.answer()
    if callback.message is None:
        return
    raw_id = (callback.data or "").removeprefix("m:edit_client:view:")
    try:
        client_id = int(raw_id)
    except ValueError:
        return
    await _show_client_view_message(callback.message, telegram_id=callback.from_user.id, client_id=client_id)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith("m:edit_client:edit_menu:"))
async def edit_menu_client(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_edit_client", step="edit_menu")
    await callback.answer()
    if callback.message is None:
        return
    raw_id = (callback.data or "").removeprefix("m:edit_client:edit_menu:")
    try:
        client_id = int(raw_id)
    except ValueError:
        await callback.answer(txt.invalid_client(), show_alert=True)
        return

    telegram_id = callback.from_user.id
    async with session_local() as session:
        master_repo = MasterRepository(session)
        try:
            master = await master_repo.get_with_clients_by_telegram_id(telegram_id)
        except MasterNotFound:
            await callback.answer(txt.master_profile_not_found(), show_alert=True)
            return

    selected = next((c.to_state_dict() for c in master.clients if int(c.id) == client_id), None)
    if selected is None:
        await callback.answer(txt.invalid_client(), show_alert=True)
        return

    await state.update_data(edit_client_results=[selected], edit_client_selected=selected)
    await safe_edit_text(
        callback.message,
        text=_render_client_card(selected),
        reply_markup=_kb_actions(),
        parse_mode="HTML",
        event="edit_client.edit_menu_failed",
    )
    await state.set_state(EditClientStates.action)


def _attendance_badge(outcome: AttendanceOutcome) -> str:
    if outcome == AttendanceOutcome.ATTENDED:
        return "✅"
    if outcome == AttendanceOutcome.NO_SHOW:
        return "❌"
    return ""


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith("m:edit_client:history:"))
async def client_history(callback: CallbackQuery) -> None:
    bind_log_context(flow="master_edit_client", step="history")
    await callback.answer()
    if callback.message is None:
        return
    raw_id = (callback.data or "").removeprefix("m:edit_client:history:")
    try:
        client_id = int(raw_id)
    except ValueError:
        return

    telegram_id = callback.from_user.id
    async with session_local() as session:
        master_repo = MasterRepository(session)
        try:
            master = await master_repo.get_by_telegram_id(telegram_id)
        except MasterNotFound:
            return

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
                (
                    f"• {slot:%d.%m.%Y %H:%M} {status_badge(booking.status)} "
                    f"{_attendance_badge(booking.attendance_outcome)}"
                ).rstrip(),
            )

    await safe_edit_text(
        callback.message,
        text="\n".join(lines).strip(),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data=f"m:edit_client:view:{int(client_id)}")],
                [InlineKeyboardButton(text=txt.btn_back_to_search(), callback_data="m:edit_client:back")],
            ],
        ),
        parse_mode="HTML",
        event="edit_client.history_failed",
    )


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith("m:edit_client:book:"))
async def book_client(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_edit_client", step="book")
    await callback.answer()
    if callback.message is None:
        return
    raw_id = (callback.data or "").removeprefix("m:edit_client:book:")
    try:
        client_id = int(raw_id)
    except ValueError:
        return

    telegram_id = callback.from_user.id
    async with session_local() as session:
        master_repo = MasterRepository(session)
        try:
            master = await master_repo.get_with_clients_by_telegram_id(telegram_id)
        except MasterNotFound:
            await callback.answer(txt.master_profile_not_found(), show_alert=True)
            return

    client = next((c for c in master.clients if int(c.id) == client_id), None)
    if client is None:
        await callback.answer(txt.invalid_client(), show_alert=True)
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
        parse_mode="HTML",
        event="edit_client.book_failed",
    )
    await state.set_state(AddBookingStates.selecting_date)


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(EditClientStates.action),
    F.data == "m:edit_client:edit_name",
)
async def start_edit_name(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_edit_client", step="edit_name_start")
    await callback.answer()
    await answer_tracked(
        callback.message,
        state,
        text=txt.ask_new_name(),
        bucket=EDIT_CLIENT_BUCKET,
        reply_markup=_kb_cancel(),
    )
    await state.set_state(EditClientStates.edit_name)


@router.message(UserRole(ActiveRole.MASTER), StateFilter(EditClientStates.edit_name))
async def save_name(message: Message, state: FSMContext, rate_limiter: RateLimiter | None = None) -> None:
    bind_log_context(flow="master_edit_client", step="edit_name_save")
    if not await rate_limit_message(message, rate_limiter, name="master_edit_client:edit_name", ttl_sec=1):
        return
    await track_message(state, message, bucket=EDIT_CLIENT_BUCKET)
    name = (message.text or "").strip()
    if not name:
        await answer_tracked(
            message,
            state,
            text=txt.name_not_recognized(),
            bucket=EDIT_CLIENT_BUCKET,
            reply_markup=_kb_cancel(),
        )
        return

    data = await state.get_data()
    selected: dict | None = data.get("edit_client_selected")
    if not selected:
        await message.answer(common_txt.context_lost())
        await state.clear()
        return

    async with active_session() as session:
        repo = ClientRepository(session)
        await repo.update_by_id(selected["id"], ClientUpdate(name=name))

    selected["name"] = name
    await state.update_data(edit_client_selected=selected)
    await cleanup_messages(state, message.bot, bucket=EDIT_CLIENT_BUCKET)
    await answer_tracked(message, state, text=txt.name_updated(), bucket=EDIT_CLIENT_BUCKET)
    await _update_card(message, state, selected)
    await state.set_state(EditClientStates.action)


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(EditClientStates.action),
    F.data == "m:edit_client:edit_phone",
)
async def start_edit_phone(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_edit_client", step="edit_phone_start")
    await callback.answer()
    await answer_tracked(
        callback.message,
        state,
        text=txt.ask_new_phone(),
        bucket=EDIT_CLIENT_BUCKET,
        reply_markup=_kb_cancel(),
    )
    await state.set_state(EditClientStates.edit_phone)


@router.message(UserRole(ActiveRole.MASTER), StateFilter(EditClientStates.edit_phone))
async def save_phone(message: Message, state: FSMContext, rate_limiter: RateLimiter | None = None) -> None:
    bind_log_context(flow="master_edit_client", step="edit_phone_save")
    if not await rate_limit_message(message, rate_limiter, name="master_edit_client:edit_phone", ttl_sec=1):
        return
    await track_message(state, message, bucket=EDIT_CLIENT_BUCKET)
    raw = (message.text or "").strip()
    phone = validate_phone(raw)
    if phone is None:
        await answer_tracked(
            message,
            state,
            text=txt.phone_not_recognized(),
            bucket=EDIT_CLIENT_BUCKET,
            reply_markup=_kb_cancel(),
        )
        return

    data = await state.get_data()
    selected: dict | None = data.get("edit_client_selected")
    if not selected:
        await message.answer(common_txt.context_lost())
        await state.clear()
        return

    telegram_id = message.from_user.id
    async with active_session() as session:
        master_repo = MasterRepository(session)
        client_repo = ClientRepository(session)
        try:
            master = await master_repo.get_by_telegram_id(telegram_id)
        except MasterNotFound:
            await message.answer(txt.master_profile_not_found())
            await state.clear()
            return

        try:
            existing = await client_repo.find_for_master_by_phone(master_id=master.id, phone=phone)
        except ClientNotFound:
            existing = None

        if existing is not None and existing.id != selected["id"]:
            await answer_tracked(
                message,
                state,
                text=txt.phone_conflict(),
                bucket=EDIT_CLIENT_BUCKET,
                reply_markup=_kb_cancel(),
            )
            return

        await client_repo.update_by_id(selected["id"], ClientUpdate(phone=phone))

    selected["phone"] = phone
    await state.update_data(edit_client_selected=selected)
    await cleanup_messages(state, message.bot, bucket=EDIT_CLIENT_BUCKET)
    await answer_tracked(message, state, text=txt.phone_updated(), bucket=EDIT_CLIENT_BUCKET)
    await _update_card(message, state, selected)
    await state.set_state(EditClientStates.action)
