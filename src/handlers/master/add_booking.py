import logging
from collections.abc import Iterable
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram_calendar import SimpleCalendar, SimpleCalendarCallback

from src.core.sa import active_session, session_local
from src.datetime_utils import to_zone
from src.repositories import MasterRepository
from src.repositories.booking import BookingRepository
from src.schemas import BookingCreate, MasterWithClients
from src.schemas.enums import BookingStatus, Timezone
from src.use_cases.master_free_slots import GetMasterFreeSlots
from src.utils import answer_tracked, cleanup_messages, track_callback_message, track_message

logger = logging.getLogger(__name__)
router = Router(name=__name__)

ADD_BOOKING_BUCKET = "master_add_booking"


class AddBookingStates(StatesGroup):
    search_client = State()
    selecting_date = State()
    selecting_slot = State()
    confirm = State()


def _filter_clients(clients: Iterable, query: str) -> list:
    q = query.lower()
    result = []
    for client in clients:
        name = getattr(client, "name", "") or ""
        phone = getattr(client, "phone", "") or ""
        if q in name.lower() or q in phone:
            result.append(client)
    return result


def _build_clients_keyboard(clients: list) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for client in clients[:10]:
        label = client.name
        if client.phone:
            label += f" ({client.phone})"
        if client.telegram_id is None:
            label += " · 🔴 оффлайн"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"m:add_booking:client:{client.id}")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="m:add_booking:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_slots_keyboard(slots: list[datetime]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, slot in enumerate(slots):
        rows.append([InlineKeyboardButton(text=slot.strftime("%H:%M"), callback_data=f"m:add_booking:slot:{index}")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="m:add_booking:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="m:add_booking:confirm"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="m:add_booking:cancel"),
            ],
        ],
    )


ASYNC_CTX_ERROR = "Что-то пошло не так, попробуй ещё раз"


async def _load_master_with_clients(telegram_id: int) -> MasterWithClients:
    async with session_local() as session:
        repo = MasterRepository(session)
        return await repo.get_with_clients_by_telegram_id(telegram_id)


async def start_add_booking(message: Message, state: FSMContext) -> None:
    logger.info(
        "master.add_booking.start",
        extra={"telegram_id": message.from_user.id if message.from_user else None},
    )
    await track_message(state, message, bucket=ADD_BOOKING_BUCKET)
    await answer_tracked(
        message,
        state,
        text="Введи имя или телефон клиента, чтобы создать запись:",
        bucket=ADD_BOOKING_BUCKET,
    )
    await state.set_state(AddBookingStates.search_client)


@router.message(StateFilter(AddBookingStates.search_client))
async def search_client(message: Message, state: FSMContext) -> None:
    await track_message(state, message, bucket=ADD_BOOKING_BUCKET)
    query = (message.text or "").strip()
    if not query:
        logger.debug(
            "master.add_booking.empty_query",
            extra={"telegram_id": message.from_user.id if message.from_user else None},
        )
        await answer_tracked(
            message,
            state,
            text="Нужно ввести имя или телефон клиента.",
            bucket=ADD_BOOKING_BUCKET,
        )
        return

    master = await _load_master_with_clients(message.from_user.id)
    matches = _filter_clients(master.clients, query)
    if not matches:
        logger.info(
            "master.add_booking.no_matches",
            extra={
                "telegram_id": message.from_user.id if message.from_user else None,
                "query": query,
            },
        )
        await answer_tracked(
            message,
            state,
            text="Не нашёл клиентов по запросу. Попробуй другое имя или телефон.",
            bucket=ADD_BOOKING_BUCKET,
        )
        return

    await state.update_data(
        master_id=master.id,
        master_slot_size=master.slot_size_min,
        master_timezone=str(master.timezone.value),
        clients=[client.model_dump() for client in matches],
    )
    await answer_tracked(
        message,
        state,
        text="Выбери клиента:",
        reply_markup=_build_clients_keyboard(matches),
        bucket=ADD_BOOKING_BUCKET,
    )


def _get_selected_client(data: dict, client_id: int):
    for raw in data.get("clients", []):
        if raw.get("id") == client_id:
            return raw
    return None


@router.callback_query(StateFilter(AddBookingStates.search_client), F.data.startswith("m:add_booking:client:"))
async def choose_client(callback: CallbackQuery, state: FSMContext) -> None:
    await track_callback_message(state, callback, bucket=ADD_BOOKING_BUCKET)
    _, _, _, client_id_str = callback.data.split(":", 3)
    try:
        client_id = int(client_id_str)
    except ValueError:
        logger.warning(
            "master.add_booking.client_parse_error",
            extra={"telegram_id": callback.from_user.id if callback.from_user else None, "raw": client_id_str},
        )
        await callback.answer(ASYNC_CTX_ERROR, show_alert=True)
        return

    data = await state.get_data()
    client = _get_selected_client(data, client_id)
    if client is None:
        logger.warning(
            "master.add_booking.client_not_in_state",
            extra={"telegram_id": callback.from_user.id if callback.from_user else None, "client_id": client_id},
        )
        await callback.answer(ASYNC_CTX_ERROR, show_alert=True)
        return

    logger.info(
        "master.add_booking.client_selected",
        extra={"telegram_id": callback.from_user.id if callback.from_user else None, "client_id": client_id},
    )
    await state.update_data(client=client)

    calendar = SimpleCalendar()
    reply_markup = await calendar.start_calendar()

    await callback.message.edit_text(
        text="Выбери дату для записи:",
        reply_markup=reply_markup,
    )
    await state.set_state(AddBookingStates.selecting_date)


@router.callback_query(StateFilter(AddBookingStates.selecting_date), SimpleCalendarCallback.filter())
async def pick_date(callback: CallbackQuery, callback_data: SimpleCalendarCallback, state: FSMContext) -> None:
    await track_callback_message(state, callback, bucket=ADD_BOOKING_BUCKET)

    calendar = SimpleCalendar()
    selected, picked_date = await calendar.process_selection(callback, callback_data)
    if not selected:
        return

    data = await state.get_data()
    master_id = data.get("master_id")
    master_timezone = data.get("master_timezone")
    if master_id is None or master_timezone is None:
        logger.warning(
            "master.add_booking.missing_master_data",
            extra={"telegram_id": callback.from_user.id if callback.from_user else None},
        )
        await callback.answer(ASYNC_CTX_ERROR, show_alert=True)
        return
    master_tz_enum = Timezone(master_timezone)

    async with session_local() as session:
        use_case = GetMasterFreeSlots(session)
        result = await use_case.execute(master_id=master_id, client_day=picked_date.date(), client_tz=master_tz_enum)

    slots = result.slots_utc
    if not slots:
        logger.info(
            "master.add_booking.no_slots",
            extra={
                "telegram_id": callback.from_user.id if callback.from_user else None,
                "master_id": master_id,
                "date": picked_date.date().isoformat(),
            },
        )
        await callback.message.edit_text(
            text="На этот день свободных слотов нет. Выбери другую дату.",
        )
        return

    await state.update_data(slots=[dt.isoformat() for dt in slots], master_day=str(result.master_day))
    await callback.message.edit_text(
        text=f"Свободные слоты на {picked_date.strftime('%d.%m.%Y')}:",
        reply_markup=_build_slots_keyboard([to_zone(dt, master_tz_enum) for dt in slots]),
    )
    await state.set_state(AddBookingStates.selecting_slot)


@router.callback_query(StateFilter(AddBookingStates.selecting_slot), F.data.startswith("m:add_booking:slot:"))
async def pick_slot(callback: CallbackQuery, state: FSMContext) -> None:
    await track_callback_message(state, callback, bucket=ADD_BOOKING_BUCKET)

    _, _, _, index_str = callback.data.split(":", 3)
    try:
        index = int(index_str)
    except ValueError:
        logger.warning(
            "master.add_booking.slot_parse_error",
            extra={"telegram_id": callback.from_user.id if callback.from_user else None, "raw": index_str},
        )
        await callback.answer(ASYNC_CTX_ERROR, show_alert=True)
        return

    data = await state.get_data()
    slots_iso: list[str] = data.get("slots", [])
    if not slots_iso or index < 0 or index >= len(slots_iso):
        logger.warning(
            "master.add_booking.slot_out_of_range",
            extra={
                "telegram_id": callback.from_user.id if callback.from_user else None,
                "index": index,
                "slots_len": len(slots_iso),
            },
        )
        await callback.answer(ASYNC_CTX_ERROR, show_alert=True)
        return

    slot_dt = datetime.fromisoformat(slots_iso[index])
    client = data.get("client", {})
    master_timezone = data.get("master_timezone")
    master_tz_enum = Timezone(master_timezone) if master_timezone else None
    slot_master_tz = to_zone(slot_dt, master_tz_enum) if master_tz_enum else slot_dt

    await state.update_data(selected_slot=slot_dt.isoformat())

    await callback.message.edit_text(
        text=(
            "Подтверди запись:\n\n"
            f"Клиент: {client.get('name')}\n"
            f"Дата/время: {slot_master_tz.strftime('%d.%m.%Y %H:%M')}"
        ),
        reply_markup=_build_confirm_keyboard(),
    )
    await state.set_state(AddBookingStates.confirm)


@router.callback_query(StateFilter(AddBookingStates.confirm), F.data == "m:add_booking:confirm")
async def confirm_booking(callback: CallbackQuery, state: FSMContext) -> None:
    await track_callback_message(state, callback, bucket=ADD_BOOKING_BUCKET)

    data = await state.get_data()
    slot_iso = data.get("selected_slot")
    client = data.get("client", {})
    master_id = data.get("master_id")
    master_slot_size = data.get("master_slot_size")
    master_timezone = data.get("master_timezone")
    master_tz_enum = Timezone(master_timezone) if master_timezone else None
    if slot_iso is None or master_id is None or not client or master_slot_size is None or master_tz_enum is None:
        logger.warning(
            "master.add_booking.missing_confirm_data",
            extra={"telegram_id": callback.from_user.id if callback.from_user else None},
        )
        await callback.answer(ASYNC_CTX_ERROR, show_alert=True)
        return

    slot_dt = datetime.fromisoformat(slot_iso)

    async with active_session() as session:
        booking_repo = BookingRepository(session)
        booking_create = BookingCreate(
            master_id=master_id,
            client_id=client["id"],
            start_at=slot_dt,
            duration_min=master_slot_size,
            status=BookingStatus.CONFIRMED,
        )
        booking = await booking_repo.create(booking_create)

    logger.info(
        "master.add_booking.created",
        extra={
            "master_id": master_id,
            "client_id": client["id"],
            "booking_id": booking.id,
            "slot_utc": slot_dt.isoformat(),
        },
    )
    client_has_tg = client.get("telegram_id") is not None

    text = "✅ Запись создана" + (" (🔴 оффлайн)" if not client_has_tg else "")

    await callback.answer(text=text, show_alert=True)

    if client_has_tg:
        client_tz_val = client.get("timezone")
        client_tz_enum = Timezone(client_tz_val) if client_tz_val else None
        slot_client = to_zone(slot_dt, client_tz_enum) if client_tz_enum else slot_dt
        await callback.bot.send_message(
            chat_id=client["telegram_id"],
            text=(
                "Вам назначена запись ✔️\n\n"
                f"<b>Дата/время:</b> {slot_client.strftime('%d.%m.%Y %H:%M')}\n"
                "Если время не подходит — свяжитесь с мастером."
            ),
        )

    await cleanup_messages(state, callback.bot, bucket=ADD_BOOKING_BUCKET)
    await state.clear()


@router.callback_query(F.data == "m:add_booking:cancel")
async def cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("❌ Создание записи отменено", show_alert=True)
    await cleanup_messages(state, callback.bot, bucket=ADD_BOOKING_BUCKET)
    await state.clear()
