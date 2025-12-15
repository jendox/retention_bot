import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import active_session, session_local
from src.repositories import ClientNotFound, ClientRepository, MasterNotFound, MasterRepository
from src.schemas import ClientUpdate
from src.utils import answer_tracked, cleanup_messages, track_message, validate_phone

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
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="m:edit_client:cancel")]],
    )


def _kb_results(clients: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for raw in clients[:10]:
        label = raw.get("name") or "Клиент"
        phone = raw.get("phone")
        if phone:
            label += f" ({phone})"
        if raw.get("telegram_id") is None:
            label += " · 🔴 оффлайн"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"m:edit_client:pick:{raw['id']}")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="m:edit_client:back")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="m:edit_client:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_actions(*, can_edit_phone: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="✏️ Изменить имя", callback_data="m:edit_client:edit_name")],
    ]
    if can_edit_phone:
        rows.append([InlineKeyboardButton(text="📞 Изменить телефон", callback_data="m:edit_client:edit_phone")])
    rows.append([InlineKeyboardButton(text="◀️ Назад к поиску", callback_data="m:edit_client:back")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="m:edit_client:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _render_client_card(client: dict) -> str:
    name = client.get("name") or "—"
    phone = client.get("phone") or "—"
    offline = "да" if client.get("telegram_id") is None else "нет"
    return (
        "Карточка клиента 👤\n\n"
        f"<b>Имя:</b> {name}\n"
        f"<b>Телефон:</b> {phone}\n"
        f"<b>Оффлайн:</b> {offline}"
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
        try:
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=_render_client_card(selected),
                reply_markup=_kb_actions(),
            )
            return
        except Exception:
            logger.debug("edit_client.update_card_failed", exc_info=True)

    card = await message.answer(_render_client_card(selected), reply_markup=_kb_actions())
    await track_message(state, card, bucket=EDIT_CLIENT_CARD_BUCKET)


async def start_edit_client(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await cleanup_messages(state, callback.bot, bucket=EDIT_CLIENT_BUCKET)
    await cleanup_messages(state, callback.bot, bucket=EDIT_CLIENT_CARD_BUCKET)
    await state.clear()
    await answer_tracked(
        callback.message,
        state,
        text="Введи имя или телефон клиента для поиска:",
        bucket=EDIT_CLIENT_BUCKET,
        reply_markup=_kb_cancel(),
    )
    await state.set_state(EditClientStates.query)


@router.message(StateFilter(EditClientStates.query))
async def process_query(message: Message, state: FSMContext) -> None:
    await track_message(state, message, bucket=EDIT_CLIENT_BUCKET)
    query = (message.text or "").strip()
    if not query:
        await answer_tracked(
            message,
            state,
            text="Нужно ввести имя или телефон клиента.",
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
            await message.answer("Профиль мастера не найден.")
            return

    q = query.lower()
    matches = [
        client.model_dump()
        for client in master.clients
        if q in (client.name or "").lower() or q in (client.phone or "")
    ]
    if not matches:
        await answer_tracked(
            message,
            state,
            text="Не нашёл клиентов по запросу. Попробуй другое имя или телефон.",
            bucket=EDIT_CLIENT_BUCKET,
            reply_markup=_kb_cancel(),
        )
        return

    await state.update_data(edit_client_results=matches)
    await answer_tracked(
        message,
        state,
        text="Нашёл клиентов. Выбери нужного:",
        bucket=EDIT_CLIENT_BUCKET,
        reply_markup=_kb_results(matches),
    )
    await state.set_state(EditClientStates.choosing)


@router.callback_query(
    StateFilter(
        EditClientStates.query,
        EditClientStates.choosing,
        EditClientStates.action,
        EditClientStates.edit_name,
        EditClientStates.edit_phone,
    ),
    F.data == "m:edit_client:cancel",
)
async def cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Окей, отменил.", show_alert=True)
    await cleanup_messages(state, callback.bot, bucket=EDIT_CLIENT_BUCKET)
    await cleanup_messages(state, callback.bot, bucket=EDIT_CLIENT_CARD_BUCKET)
    await state.clear()


@router.callback_query(
    StateFilter(
        EditClientStates.query,
        EditClientStates.choosing,
        EditClientStates.action,
        EditClientStates.edit_name,
        EditClientStates.edit_phone,
    ),
    F.data == "m:edit_client:back",
)
async def back(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await cleanup_messages(state, callback.bot, bucket=EDIT_CLIENT_BUCKET)
    await cleanup_messages(state, callback.bot, bucket=EDIT_CLIENT_CARD_BUCKET)
    await state.clear()
    await answer_tracked(
        callback.message,
        state,
        text="Введи имя или телефон клиента для поиска:",
        bucket=EDIT_CLIENT_BUCKET,
        reply_markup=_kb_cancel(),
    )
    await state.set_state(EditClientStates.query)


@router.callback_query(StateFilter(EditClientStates.choosing), F.data.startswith("m:edit_client:pick:"))
async def pick_client(callback: CallbackQuery, state: FSMContext) -> None:
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
        await callback.answer("Некорректный клиент.", show_alert=True)
        return

    data = await state.get_data()
    results: list[dict] = data.get("edit_client_results", [])
    selected = next((c for c in results if c.get("id") == client_id), None)
    if selected is None:
        await callback.answer("Не нашёл клиента в списке, попробуй заново.", show_alert=True)
        return

    await state.update_data(edit_client_selected=selected)
    if callback.message:
        await callback.message.edit_text(
            text=_render_client_card(selected),
            reply_markup=_kb_actions(),
        )
    await state.set_state(EditClientStates.action)


@router.callback_query(StateFilter(EditClientStates.action), F.data == "m:edit_client:edit_name")
async def start_edit_name(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await answer_tracked(
        callback.message,
        state,
        text="Введи новое имя клиента:",
        bucket=EDIT_CLIENT_BUCKET,
        reply_markup=_kb_cancel(),
    )
    await state.set_state(EditClientStates.edit_name)


@router.message(StateFilter(EditClientStates.edit_name))
async def save_name(message: Message, state: FSMContext) -> None:
    await track_message(state, message, bucket=EDIT_CLIENT_BUCKET)
    name = (message.text or "").strip()
    if not name:
        await answer_tracked(
            message,
            state,
            text="Имя не понял 😅 Введи, пожалуйста, имя клиента.",
            bucket=EDIT_CLIENT_BUCKET,
            reply_markup=_kb_cancel(),
        )
        return

    data = await state.get_data()
    selected: dict | None = data.get("edit_client_selected")
    if not selected:
        await message.answer("Контекст потерян, начни заново.")
        await state.clear()
        return

    async with active_session() as session:
        repo = ClientRepository(session)
        await repo.update_by_id(selected["id"], ClientUpdate(name=name))

    selected["name"] = name
    await state.update_data(edit_client_selected=selected)
    await cleanup_messages(state, message.bot, bucket=EDIT_CLIENT_BUCKET)
    await answer_tracked(message, state, text="✅ Имя обновлено.", bucket=EDIT_CLIENT_BUCKET)
    await _update_card(message, state, selected)
    await state.set_state(EditClientStates.action)


@router.callback_query(StateFilter(EditClientStates.action), F.data == "m:edit_client:edit_phone")
async def start_edit_phone(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await answer_tracked(
        callback.message,
        state,
        text="Введи новый телефон клиента (в формате <code>375291234567</code>):",
        bucket=EDIT_CLIENT_BUCKET,
        reply_markup=_kb_cancel(),
    )
    await state.set_state(EditClientStates.edit_phone)


@router.message(StateFilter(EditClientStates.edit_phone))
async def save_phone(message: Message, state: FSMContext) -> None:
    await track_message(state, message, bucket=EDIT_CLIENT_BUCKET)
    raw = (message.text or "").strip()
    phone = validate_phone(raw)
    if phone is None:
        await answer_tracked(
            message,
            state,
            text="Не смог разобрать номер 🤔\n\nВведи номер в формате <code>375291234567</code>:",
            bucket=EDIT_CLIENT_BUCKET,
            reply_markup=_kb_cancel(),
        )
        return

    data = await state.get_data()
    selected: dict | None = data.get("edit_client_selected")
    if not selected:
        await message.answer("Контекст потерян, начни заново.")
        await state.clear()
        return

    telegram_id = message.from_user.id
    async with active_session() as session:
        master_repo = MasterRepository(session)
        client_repo = ClientRepository(session)
        try:
            master = await master_repo.get_by_telegram_id(telegram_id)
        except MasterNotFound:
            await message.answer("Профиль мастера не найден.")
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
                text="У тебя уже есть клиент с таким телефоном. Телефон должен быть уникален в твоей базе.",
                bucket=EDIT_CLIENT_BUCKET,
                reply_markup=_kb_cancel(),
            )
            return

        await client_repo.update_by_id(selected["id"], ClientUpdate(phone=phone))

    selected["phone"] = phone
    await state.update_data(edit_client_selected=selected)
    await cleanup_messages(state, message.bot, bucket=EDIT_CLIENT_BUCKET)
    await answer_tracked(message, state, text="✅ Телефон обновлён.", bucket=EDIT_CLIENT_BUCKET)
    await _update_card(message, state, selected)
    await state.set_state(EditClientStates.action)
