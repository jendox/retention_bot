import re
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
from src.handlers.master.list_clients import CLIENTS_CB_PREFIX
from src.handlers.shared.guards import rate_limit_callback, rate_limit_message
from src.handlers.shared.ui import safe_bot_edit_message_text, safe_edit_text
from src.models import Booking as BookingEntity
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.rate_limiter import RateLimiter
from src.repositories import ClientNotFound, ClientRepository, MasterClientRepository, MasterNotFound, MasterRepository
from src.schemas import ClientUpdate
from src.schemas.enums import AttendanceOutcome, status_badge
from src.texts import common as common_txt, edit_client as txt
from src.texts.buttons import btn_back
from src.texts.master_client_card import ClientHints, ClientSummary, card as render_client_view
from src.user_context import ActiveRole
from src.utils import (
    answer_tracked,
    cleanup_messages,
    format_phone_display,
    format_phone_e164,
    track_message,
    validate_phone,
)

router = Router(name=__name__)
ev = EventLogger(__name__)

EDIT_CLIENT_BUCKET = "master_edit_client"
EDIT_CLIENT_CARD_BUCKET = "master_edit_client_card"
_GC_BUCKETS_KEY = "_gc_buckets"
EDIT_CLIENT_MAIN_KEY = "master_edit_client_main"
EDIT_CLIENT_ORIGIN_KEY = "master_edit_client_origin"


class EditClientStates(StatesGroup):
    query = State()
    choosing = State()
    action = State()
    edit_name = State()
    edit_phone = State()


async def _load_master_with_clients_and_aliases(*, telegram_id: int):
    async with session_local() as session:
        master_repo = MasterRepository(session)
        try:
            master = await master_repo.get_with_clients_by_telegram_id(telegram_id)
        except MasterNotFound:
            return None
        aliases = await MasterClientRepository(session).get_client_aliases_for_master(master_id=int(master.id))
    if aliases:
        for client in master.clients:
            alias = aliases.get(int(client.id))
            if alias:
                client.name = alias
    return master


def _kb_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=btn_back(), callback_data="m:edit_client:cancel")],
        ],
    )


def _kb_results(clients: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for raw in clients[:10]:
        label = raw.get("name") or common_txt.label_default_client()
        phone = raw.get("phone")
        if phone:
            label += f" ({format_phone_display(str(phone))})"
        if raw.get("telegram_id") is None:
            label += common_txt.label_offline_badge()
        rows.append([InlineKeyboardButton(text=label, callback_data=f"m:edit_client:pick:{raw['id']}")])
    rows.append([InlineKeyboardButton(text=txt.btn_back_to_search(), callback_data="m:edit_client:back")])
    rows.append([InlineKeyboardButton(text=btn_back(), callback_data="m:edit_client:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_actions(*, can_edit_phone: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=txt.btn_edit_name(), callback_data="m:edit_client:edit_name")],
    ]
    if can_edit_phone:
        rows.append([InlineKeyboardButton(text=txt.btn_edit_phone(), callback_data="m:edit_client:edit_phone")])
    rows.append([InlineKeyboardButton(text=txt.btn_back_to_search(), callback_data="m:edit_client:back")])
    rows.append([InlineKeyboardButton(text=btn_back(), callback_data="m:edit_client:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_edit_menu(*, client_id: int, can_edit_phone: bool, back_cb: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=txt.btn_edit_name(), callback_data="m:edit_client:edit_name")],
    ]
    if can_edit_phone:
        rows.append([InlineKeyboardButton(text=txt.btn_edit_phone(), callback_data="m:edit_client:edit_phone")])
    rows.append([InlineKeyboardButton(text=btn_back(), callback_data=str(back_cb))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _get_main_ref(data: dict, *, telegram_id: int) -> tuple[int, int] | None:
    ref = data.get(EDIT_CLIENT_MAIN_KEY) or {}
    chat_id = ref.get("chat_id") or telegram_id
    message_id = ref.get("message_id")
    if message_id is None:
        return None
    return int(chat_id), int(message_id)


async def _set_main_ref(state: FSMContext, *, chat_id: int, message_id: int) -> None:
    await state.update_data(**{EDIT_CLIENT_MAIN_KEY: {"chat_id": int(chat_id), "message_id": int(message_id)}})


async def _show_client_edit_menu_message(
    message: Message,
    state: FSMContext,
    *,
    telegram_id: int,
    client_id: int,
) -> bool:
    payload = await _client_view_payload(telegram_id=telegram_id, client_id=client_id)
    if payload is None:
        return False
    text, client_tg_id, can_edit_phone = payload
    data = await state.get_data()
    back_cb = _origin_back_cb(data, client_id=client_id)
    return await safe_edit_text(
        message,
        text=text,
        reply_markup=_kb_edit_menu(
            client_id=int(client_id),
            can_edit_phone=bool(can_edit_phone),
            back_cb=back_cb,
        ),
        parse_mode="HTML",
        event="edit_client.edit_menu_failed",
    )


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
            [InlineKeyboardButton(text=btn_back(), callback_data="m:edit_client:cancel")],
        ],
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_edit_input(*, client_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=btn_back(),
                    callback_data=f"m:edit_client:edit_menu:{int(client_id)}",
                ),
            ],
        ],
    )


async def _edit_main(
    state: FSMContext,
    *,
    bot,
    telegram_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    event: str,
) -> bool:
    data = await state.get_data()
    ref = _get_main_ref(data, telegram_id=telegram_id)
    if ref is None:
        return False
    chat_id, message_id = ref
    return await safe_bot_edit_message_text(
        bot,
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode="HTML",
        event=event,
    )


def _parse_open_direct(raw: str) -> tuple[int, int | None, int | None] | None:
    """
    Supports:
    - "<client_id>"
    - "<client_id>:p:<page>:c:<chunk>" (origin from master clients list/card)
    """
    match = re.fullmatch(r"(?P<id>\d+)(?::p:(?P<page>\d+):c:(?P<chunk>\d+))?", raw.strip())
    if match is None:
        return None
    client_id = int(match.group("id"))
    page = match.group("page")
    chunk = match.group("chunk")
    return client_id, (int(page) if page is not None else None), (int(chunk) if chunk is not None else None)


async def _store_origin_if_present(state: FSMContext, *, page: int | None, chunk: int | None) -> None:
    if page is None or chunk is None:
        return
    await state.update_data(
        **{
            EDIT_CLIENT_ORIGIN_KEY: {
                "source": "list_clients",
                "page": int(page),
                "chunk": int(chunk),
            },
        },
    )


async def _get_selected_client_for_master(*, telegram_id: int, client_id: int) -> dict | None | str:
    master = await _load_master_with_clients_and_aliases(telegram_id=telegram_id)
    if master is None:
        return "master_not_found"

    selected = next((c.to_state_dict() for c in master.clients if int(c.id) == int(client_id)), None)
    return selected


def _origin_back_cb(data: dict, *, client_id: int) -> str:
    origin = data.get(EDIT_CLIENT_ORIGIN_KEY) or {}
    if origin.get("source") == "list_clients":
        page = origin.get("page")
        chunk = origin.get("chunk")
        if page is not None and chunk is not None:
            # Return back to the clients card in master list flow.
            return f"{CLIENTS_CB_PREFIX}s:open:{int(client_id)}:p:{int(page)}:c:{int(chunk)}"
    return f"m:edit_client:view:{int(client_id)}"


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


async def _client_view_payload(
    *,
    telegram_id: int,
    client_id: int,
) -> tuple[str, int | None, bool] | None:
    master = await _load_master_with_clients_and_aliases(telegram_id=telegram_id)
    if master is None:
        return None

    client = next((c for c in master.clients if int(c.id) == int(client_id)), None)
    if client is None:
        return None

    last_visit_at, visits_count, no_show_count = await _fetch_client_stats(
        master_id=int(master.id),
        client_id=int(client_id),
    )
    last_visit_day = to_zone(last_visit_at, master.timezone).date() if last_visit_at is not None else None

    name_safe = common_txt.label_default_client() if not getattr(client, "name", None) else str(client.name)
    phone_display = format_phone_e164(str(client.phone)) if getattr(client, "phone", None) else None

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
    return text, client.telegram_id, bool(client.telegram_id is None)


async def _show_client_view_message(
    message: Message,
    *,
    telegram_id: int,
    client_id: int,
) -> bool:
    payload = await _client_view_payload(telegram_id=telegram_id, client_id=client_id)
    if payload is None:
        return False
    text, client_tg_id, _can_edit_phone = payload
    return await safe_edit_text(
        message,
        text=text,
        reply_markup=_kb_client_view(client_id=int(client_id), telegram_id=client_tg_id),
        parse_mode="HTML",
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
    can_edit_phone = selected.get("telegram_id") is None
    chat_id, message_id = await _get_last_card_message_ref(state)
    if chat_id is not None and message_id is not None:
        ok = await safe_bot_edit_message_text(
            message.bot,
            chat_id=int(chat_id),
            message_id=int(message_id),
            text=_render_client_card(selected),
            reply_markup=_kb_actions(can_edit_phone=bool(can_edit_phone)),
            event="edit_client.update_card_failed",
        )
        if ok:
            return

    card = await message.answer(
        _render_client_card(selected),
        reply_markup=_kb_actions(can_edit_phone=bool(can_edit_phone)),
        parse_mode="HTML",
    )
    await track_message(state, card, bucket=EDIT_CLIENT_CARD_BUCKET)


async def start_edit_client(
    callback: CallbackQuery,
    state: FSMContext,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="master_edit_client", step="start")
    if not await rate_limit_callback(callback, rate_limiter, name="master_edit_client:start", ttl_sec=2):
        return
    ev.info("master_edit_client.start")
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
        ev.debug("master_edit_client.input_invalid", field="query", reason="empty")
        await answer_tracked(
            message,
            state,
            text=txt.name_or_phone_required(),
            bucket=EDIT_CLIENT_BUCKET,
            reply_markup=_kb_cancel(),
        )
        return

    # Keep the chat clean: remove the initial "enter name/phone" prompt and previous search messages.
    await cleanup_messages(state, message.bot, bucket=EDIT_CLIENT_BUCKET)

    telegram_id = message.from_user.id
    master = await _load_master_with_clients_and_aliases(telegram_id=telegram_id)
    if master is None:
        ev.warning("master_edit_client.master_not_found")
        await message.answer(txt.master_profile_not_found())
        return

    q = query.lower()
    matches = [
        client.to_state_dict()
        for client in master.clients
        if q in (client.name or "").lower() or q in (client.phone or "")
    ]
    if not matches:
        ev.info("master_edit_client.search_result", outcome="no_matches")
        await answer_tracked(
            message,
            state,
            text=txt.no_clients_found(),
            bucket=EDIT_CLIENT_BUCKET,
            reply_markup=_kb_cancel(),
        )
        return

    await state.update_data(edit_client_results=matches)
    ev.info("master_edit_client.search_result", outcome="matches", matches=len(matches))
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
    await callback.answer()
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

    raw = (callback.data or "").removeprefix("m:edit_client:open:")
    parsed = _parse_open_direct(raw)
    if parsed is None:
        await callback.answer(txt.invalid_client(), show_alert=True)
        return
    client_id, origin_page, origin_chunk = parsed

    await cleanup_messages(state, callback.bot, bucket=EDIT_CLIENT_BUCKET)
    await cleanup_messages(state, callback.bot, bucket=EDIT_CLIENT_CARD_BUCKET)
    await state.clear()

    telegram_id = callback.from_user.id
    selected = await _get_selected_client_for_master(telegram_id=telegram_id, client_id=client_id)
    if selected == "master_not_found":
        await callback.answer(txt.master_profile_not_found(), show_alert=True)
        return
    if selected is None:
        await callback.answer(txt.invalid_client(), show_alert=True)
        return

    await state.update_data(edit_client_results=[selected], edit_client_selected=selected)
    await _store_origin_if_present(state, page=origin_page, chunk=origin_chunk)
    await _set_main_ref(state, chat_id=callback.message.chat.id, message_id=callback.message.message_id)
    ok = await _show_client_edit_menu_message(
        callback.message,
        state,
        telegram_id=callback.from_user.id,
        client_id=int(client_id),
    )
    if not ok:
        await callback.answer(common_txt.generic_error(), show_alert=True)
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
        await _set_main_ref(state, chat_id=callback.message.chat.id, message_id=callback.message.message_id)
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
    master = await _load_master_with_clients_and_aliases(telegram_id=telegram_id)
    if master is None:
        await callback.answer(txt.master_profile_not_found(), show_alert=True)
        return

    selected = next((c.to_state_dict() for c in master.clients if int(c.id) == client_id), None)
    if selected is None:
        await callback.answer(txt.invalid_client(), show_alert=True)
        return

    await state.update_data(edit_client_results=[selected], edit_client_selected=selected)
    await _set_main_ref(state, chat_id=callback.message.chat.id, message_id=callback.message.message_id)
    ok = await _show_client_edit_menu_message(
        callback.message,
        state,
        telegram_id=callback.from_user.id,
        client_id=int(client_id),
    )
    if not ok:
        await callback.answer(common_txt.generic_error(), show_alert=True)
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
                [InlineKeyboardButton(text=btn_back(), callback_data="m:edit_client:cancel")],
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

    from src.handlers.master import add_booking as add_booking_h

    await state.clear()
    await state.update_data(
        master_id=int(master.id),
        master_slot_size=int(master.slot_size_min),
        master_timezone=str(master.timezone.value),
        master_day=None,
        client=client.to_state_dict(),
        confirm_in_progress=False,
    )
    reply_markup = await add_booking_h._calendar_markup(state)
    await safe_edit_text(
        callback.message,
        text=add_booking_h._calendar_prompt_text(),
        reply_markup=reply_markup,
        parse_mode="HTML",
        event="edit_client.book_failed",
    )
    await state.set_state(add_booking_h.AddBookingStates.selecting_date)


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(EditClientStates.action),
    F.data == "m:edit_client:edit_name",
)
async def start_edit_name(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_edit_client", step="edit_name_start")
    await callback.answer()
    data = await state.get_data()
    selected: dict | None = data.get("edit_client_selected")
    if callback.message is not None:
        await _set_main_ref(state, chat_id=callback.message.chat.id, message_id=callback.message.message_id)
    if not selected:
        await callback.answer(common_txt.context_lost(), show_alert=True)
        await state.clear()
        return
    prompt = txt.ask_new_alias() if selected.get("telegram_id") is not None else txt.ask_new_name()
    await _edit_main(
        state,
        bot=callback.bot,
        telegram_id=callback.from_user.id,
        text=prompt,
        reply_markup=_kb_edit_input(client_id=int(selected["id"])),
        event="edit_client.edit_name_prompt_failed",
    )
    await state.set_state(EditClientStates.edit_name)


@router.message(UserRole(ActiveRole.MASTER), StateFilter(EditClientStates.edit_name))
async def save_name(message: Message, state: FSMContext, rate_limiter: RateLimiter | None = None) -> None:
    bind_log_context(flow="master_edit_client", step="edit_name_save")
    if not await rate_limit_message(message, rate_limiter, name="master_edit_client:edit_name", ttl_sec=1):
        return
    name = (message.text or "").strip()
    if not name:
        data = await state.get_data()
        selected: dict | None = data.get("edit_client_selected")
        if selected is None:
            await message.answer(common_txt.context_lost())
            await state.clear()
            return
        await _edit_main(
            state,
            bot=message.bot,
            telegram_id=message.from_user.id,
            text=txt.name_not_recognized(),
            reply_markup=_kb_edit_input(client_id=int(selected["id"])),
            event="edit_client.edit_name_invalid_failed",
        )
        return

    data = await state.get_data()
    selected: dict | None = data.get("edit_client_selected")
    if not selected:
        await message.answer(common_txt.context_lost())
        await state.clear()
        return

    await track_message(state, message, bucket=EDIT_CLIENT_BUCKET)

    async with active_session() as session:
        result = await _update_name_for_master(
            session=session,
            telegram_id=message.from_user.id,
            selected=selected,
            name=name,
        )
        if result == "master_not_found":
            await cleanup_messages(state, message.bot, bucket=EDIT_CLIENT_BUCKET)
            await message.answer(txt.master_profile_not_found())
            await state.clear()
            return
        if result != "ok":
            await cleanup_messages(state, message.bot, bucket=EDIT_CLIENT_BUCKET)
            await message.answer(common_txt.generic_error())
            await state.clear()
            return

    selected["name"] = name
    await state.update_data(edit_client_selected=selected)
    await cleanup_messages(state, message.bot, bucket=EDIT_CLIENT_BUCKET)
    await _refresh_edit_menu(
        message,
        state,
        telegram_id=message.from_user.id,
        client_id=int(selected["id"]),
    )


async def _update_name_for_master(
    *,
    session,
    telegram_id: int,
    selected: dict,
    name: str,
) -> str:
    repo = ClientRepository(session)
    if selected.get("telegram_id") is None:
        await repo.update_by_id(int(selected["id"]), ClientUpdate(name=name))
        return "ok"

    master_repo = MasterRepository(session)
    try:
        master = await master_repo.get_by_telegram_id(telegram_id)
    except MasterNotFound:
        return "master_not_found"

    updated = await MasterClientRepository(session).set_client_alias(
        master_id=int(master.id),
        client_id=int(selected["id"]),
        alias=name,
    )
    return "ok" if updated else "not_attached"


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(EditClientStates.action),
    F.data == "m:edit_client:edit_phone",
)
async def start_edit_phone(callback: CallbackQuery, state: FSMContext) -> None:
    bind_log_context(flow="master_edit_client", step="edit_phone_start")
    await callback.answer()
    data = await state.get_data()
    selected: dict | None = data.get("edit_client_selected")
    if callback.message is not None:
        await _set_main_ref(state, chat_id=callback.message.chat.id, message_id=callback.message.message_id)
    if not selected:
        await callback.answer(common_txt.context_lost(), show_alert=True)
        await state.clear()
        return
    if selected.get("telegram_id") is not None:
        await callback.answer(txt.phone_edit_not_allowed_for_telegram_client())
        return
    await _edit_main(
        state,
        bot=callback.bot,
        telegram_id=callback.from_user.id,
        text=txt.ask_new_phone(),
        reply_markup=_kb_edit_input(client_id=int(selected["id"])),
        event="edit_client.edit_phone_prompt_failed",
    )
    await state.set_state(EditClientStates.edit_phone)


async def _update_phone_for_master(
    *,
    telegram_id: int,
    client_id: int,
    phone: str,
) -> str:
    async with active_session() as session:
        master_repo = MasterRepository(session)
        client_repo = ClientRepository(session)
        try:
            master = await master_repo.get_by_telegram_id(telegram_id)
        except MasterNotFound:
            return "master_not_found"

        try:
            existing = await client_repo.find_for_master_by_phone(master_id=master.id, phone=phone)
        except ClientNotFound:
            existing = None

        if existing is not None and int(existing.id) != int(client_id):
            return "phone_conflict"

        await client_repo.update_by_id(int(client_id), ClientUpdate(phone=phone))
        return "ok"


async def _refresh_edit_menu(
    message: Message,
    state: FSMContext,
    *,
    telegram_id: int,
    client_id: int,
) -> None:
    payload = await _client_view_payload(telegram_id=telegram_id, client_id=client_id)
    if payload is None:
        await message.answer(common_txt.generic_error())
        await state.clear()
        return
    text, _client_tg_id, can_edit_phone = payload
    data = await state.get_data()
    back_cb = _origin_back_cb(data, client_id=client_id)
    await _edit_main(
        state,
        bot=message.bot,
        telegram_id=telegram_id,
        text=text,
        reply_markup=_kb_edit_menu(
            client_id=client_id,
            can_edit_phone=bool(can_edit_phone),
            back_cb=back_cb,
        ),
        event="edit_client.edit_menu_refresh_failed",
    )
    await state.set_state(EditClientStates.action)


async def _deny_phone_edit(
    message: Message,
    state: FSMContext,
    *,
    telegram_id: int,
    client_id: int,
) -> None:
    await cleanup_messages(state, message.bot, bucket=EDIT_CLIENT_BUCKET)
    await _edit_main(
        state,
        bot=message.bot,
        telegram_id=telegram_id,
        text=txt.phone_edit_not_allowed_for_telegram_client(),
        reply_markup=_kb_edit_menu(client_id=client_id, can_edit_phone=False),
        event="edit_client.edit_phone_not_allowed_failed",
    )
    await state.set_state(EditClientStates.action)


async def _save_phone_offline_client(
    message: Message,
    state: FSMContext,
    selected: dict,
    *,
    telegram_id: int,
    client_id: int,
) -> None:
    raw = (message.text or "").strip()
    phone = validate_phone(raw)
    if phone is None:
        await cleanup_messages(state, message.bot, bucket=EDIT_CLIENT_BUCKET)
        await _edit_main(
            state,
            bot=message.bot,
            telegram_id=telegram_id,
            text=txt.phone_not_recognized(),
            reply_markup=_kb_edit_input(client_id=client_id),
            event="edit_client.edit_phone_invalid_failed",
        )
        return

    result = await _update_phone_for_master(telegram_id=telegram_id, client_id=client_id, phone=phone)
    if result == "master_not_found":
        await cleanup_messages(state, message.bot, bucket=EDIT_CLIENT_BUCKET)
        await message.answer(txt.master_profile_not_found())
        await state.clear()
        return
    if result == "phone_conflict":
        await cleanup_messages(state, message.bot, bucket=EDIT_CLIENT_BUCKET)
        await _edit_main(
            state,
            bot=message.bot,
            telegram_id=telegram_id,
            text=f"{txt.phone_conflict()}\n\n{txt.ask_new_phone()}",
            reply_markup=_kb_edit_input(client_id=client_id),
            event="edit_client.edit_phone_conflict_failed",
        )
        return

    selected["phone"] = phone
    await state.update_data(edit_client_selected=selected)
    await cleanup_messages(state, message.bot, bucket=EDIT_CLIENT_BUCKET)
    await _refresh_edit_menu(message, state, telegram_id=telegram_id, client_id=client_id)


@router.message(UserRole(ActiveRole.MASTER), StateFilter(EditClientStates.edit_phone))
async def save_phone(message: Message, state: FSMContext, rate_limiter: RateLimiter | None = None) -> None:
    bind_log_context(flow="master_edit_client", step="edit_phone_save")
    if not await rate_limit_message(message, rate_limiter, name="master_edit_client:edit_phone", ttl_sec=1):
        return
    data = await state.get_data()
    selected: dict | None = data.get("edit_client_selected")
    if not selected:
        await message.answer(common_txt.context_lost())
        await state.clear()
        return

    await track_message(state, message, bucket=EDIT_CLIENT_BUCKET)
    telegram_id = message.from_user.id
    client_id = int(selected["id"])

    if selected.get("telegram_id") is not None:
        await _deny_phone_edit(message, state, telegram_id=telegram_id, client_id=client_id)
    else:
        await _save_phone_offline_client(
            message,
            state,
            selected,
            telegram_id=telegram_id,
            client_id=client_id,
        )
