import logging
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram_calendar import SimpleCalendar, SimpleCalendarCallback
from sqlalchemy.exc import IntegrityError

from src.core.sa import active_session, session_local
from src.datetime_utils import get_timezone, to_zone
from src.handlers.client.messages import CLIENT_NOT_FOUND_MESSAGE
from src.repositories import ClientNotFound, ClientRepository, MasterRepository
from src.repositories.booking import BookingRepository
from src.schemas import BookingCreate, Master
from src.schemas.enums import Timezone
from src.use_cases.master_free_slots import GetMasterFreeSlots
from src.utils import answer_tracked, cleanup_messages, track_callback_message, track_message

router = Router(name=__name__)
logger = logging.getLogger(__name__)


class ClientBooking(StatesGroup):
    selecting_master = State()
    selecting_date = State()
    selecting_slot = State()
    confirm = State()


def build_masters_keyboard(masters: list[Master]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for master in masters:
        rows.append([
            InlineKeyboardButton(text=master.name, callback_data=f"book:master:{master.id}"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_slots_keyboard(slots: list[datetime]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for index, slot_dt in enumerate(slots):
        label = slot_dt.strftime("%H:%M")
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"book:slot:{index}"),
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="book:confirm"),
                InlineKeyboardButton(text="❌ Отменить", callback_data="book:cancel"),
            ],
        ],
    )


def build_master_booking_review_keyboard(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"m:booking:{booking_id}:confirm"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"m:booking:{booking_id}:decline"),
            ],
        ],
    )


@router.message(F.text == "➕ Записаться")
async def client_add_booking(message: Message, state: FSMContext) -> None:
    await track_message(state, message)
    telegram_id = message.from_user.id
    async with session_local() as session:
        repo = ClientRepository(session)
        try:
            client = await repo.get_details_by_telegram_id(telegram_id)
        except ClientNotFound:
            await message.answer(CLIENT_NOT_FOUND_MESSAGE)
            return
        masters = client.masters

    if not masters:
        await message.answer(
            "У тебя пока нет подключенных мастеров 👀\n"
            "Попроси мастера прислать тебе ссылку для записи в BeautyDesk.",
        )
        return

    await state.update_data(
        client_id=client.id,
        client_timezone=client.timezone,
        client_name=client.name,
    )

    if len(masters) == 1:
        master = masters[0]
        await start_booking_for_master(message, state, master.id)
        return

    await answer_tracked(
        message,
        state,
        text="Выбери мастера, к которому хочешь записаться 💇‍♀️",
        reply_markup=build_masters_keyboard(masters),
    )
    await state.set_state(ClientBooking.selecting_master)


@router.callback_query(
    StateFilter(ClientBooking.selecting_master),
    F.data.startswith("book:master:"),
)
async def booking_select_master(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await track_callback_message(state, callback)

    _, _, master_id = callback.data.split(":")
    master_id = int(master_id)
    await start_booking_for_master(callback.message, state, master_id)


async def start_booking_for_master(
    message: Message,
    state: FSMContext,
    master_id: int,
) -> None:
    await state.update_data(master_id=master_id)

    calendar = SimpleCalendar()
    reply_markup = await calendar.start_calendar()

    await answer_tracked(
        message,
        state,
        text="Выбери дату для записи 📅",
        reply_markup=reply_markup,
    )
    await state.set_state(ClientBooking.selecting_date)


@router.callback_query(
    StateFilter(ClientBooking.selecting_date),
    SimpleCalendarCallback.filter(),
)
async def process_booking_calendar(
    callback: CallbackQuery,
    callback_data: SimpleCalendarCallback,
    state: FSMContext,
) -> None:
    await track_callback_message(state, callback)

    calendar = SimpleCalendar()
    selected, picked_date = await calendar.process_selection(callback, callback_data)
    if not selected:
        return

    data = await state.get_data()
    master_id = data.get("master_id")
    client_timezone: Timezone = data.get("client_timezone")
    if master_id is None or client_timezone is None:
        await callback.answer("Что-то пошло не так, попробуй ещё раз", show_alert=True)
        return

    client_tz_info = get_timezone(str(client_timezone.value))
    client_day = picked_date.date() if picked_date.tzinfo is None \
        else picked_date.astimezone(client_tz_info).date()

    today_client = datetime.now(tz=client_tz_info).date()
    min_date = today_client + timedelta(days=1)
    max_date = today_client + timedelta(days=45)
    if not (min_date <= client_day <= max_date):
        await callback.answer(
            text=f"Можно выбрать дату с {min_date.strftime('%d.%m.%Y')} "
                 f"по {max_date.strftime('%d.%m.%Y')}",
            show_alert=True,
        )
        return

    await callback.answer()

    async with session_local() as session:
        use_case = GetMasterFreeSlots(session)
        result = await use_case.execute(
            master_id=master_id, client_day=client_day, client_tz=client_timezone,
        )

    if not result.slots_utc:
        await callback.message.edit_text(
            text="На этот день свободных слотов нет 😕\n"
                 "Попробуй выбрать другую дату.",
        )
        return

    slots_iso = [dt.isoformat() for dt in result.slots_utc]
    await state.update_data(
        booking_slots_utc=slots_iso,
        client_day=client_day,
    )

    await callback.message.edit_text(
        text=f"Свободные слоты на {client_day.strftime('%d.%m.%Y')} ⏰\n"
             "Выбери удобное время:",
        reply_markup=build_slots_keyboard(result.slots_for_client),
    )
    await state.set_state(ClientBooking.selecting_slot)


@router.callback_query(
    StateFilter(ClientBooking.selecting_slot),
    F.data.startswith("book:slot:"),
)
async def booking_select_slot(callback: CallbackQuery, state: FSMContext) -> None:
    await track_callback_message(state, callback)

    _, _, index = callback.data.split(":")
    index = int(index)

    data = await state.get_data()
    slots_iso: list[str] = data.get("booking_slots_utc", [])
    client_timezone: Timezone = data.get("client_timezone")

    if client_timezone is None or not slots_iso:
        await callback.answer("Что-то пошло не так, попробуй ещё раз", show_alert=True)
        return

    if index < 0 or index >= len(slots_iso):
        await callback.answer("Некорректный слот, попробуй ещё раз", show_alert=True)
        return

    await callback.answer()

    slot_dt_utc = datetime.fromisoformat(slots_iso[index])  # utc aware
    slot_dt_client = slot_dt_utc.astimezone(get_timezone(str(client_timezone.value)))

    await state.update_data(selected_slot_index=index)

    text = (
        f"Подтверди запись 👇\n\n"
        f"<b>Дата:</b> {slot_dt_client.strftime('%d.%m.%Y')}\n"
        f"<b>Время:</b> {slot_dt_client.strftime('%H:%M')}\n"
    )

    await callback.message.edit_text(text, reply_markup=build_confirm_keyboard())
    await state.set_state(ClientBooking.confirm)


@router.callback_query(
    StateFilter(ClientBooking.confirm),
    F.data == "book:cancel",
)
async def booking_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Запись не сохранена.")
    await cleanup_messages(state, callback.bot)
    await state.clear()
    await callback.message.answer(
        text="Окей, запись отменена. Если передумаешь — просто нажми «➕ Записаться» 🙂",
    )


@router.callback_query(
    StateFilter(ClientBooking.confirm),
    F.data == "book:confirm",
)
async def booking_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    await track_callback_message(state, callback)

    data = await state.get_data()
    slots_iso: list[str] = data.get("booking_slots_utc", [])
    index = data.get("selected_slot_index")
    master_id = data.get("master_id")
    client_id = data.get("client_id")
    client_name = data.get("client_name", "")

    if master_id is None or index is None or client_id is None or not slots_iso:
        await callback.answer("Что-то пошло не так, попробуй ещё раз", show_alert=True)
        return

    slot_dt_utc = datetime.fromisoformat(slots_iso[index])

    async with active_session() as session:
        master_repo = MasterRepository(session)
        booking_repo = BookingRepository(session)

        master = await master_repo.get_by_id(master_id)

        booking_create = BookingCreate(
            master_id=master_id,
            client_id=client_id,
            start_at=slot_dt_utc,
            duration_min=master.slot_size_min,
        )
        try:
            booking = await booking_repo.create(booking_create)
        except IntegrityError:
            await callback.answer(
                text="Упс — этот слот только что заняли 😕\n"
                     "Пожалуйста, выбери другое время.",
                show_alert=True,
            )
            return

        logger.info(
            "booking.created",
            extra={
                "booking_id": booking.id,
                "master_id": master_id,
                "client_id": client_id,
            },
        )
    await callback.answer()

    await cleanup_messages(state, callback.bot)
    await state.clear()

    await callback.message.answer(
        "Готово! 🎉\n\n"
        "Запись создана и отправлена мастеру на подтверждение.\n"
        "Как только мастер подтвердит (или отклонит) — я сразу сообщу.\n\n"
        "Статус можно посмотреть в разделе «📋 Мои записи».",
    )

    # Send a notification to the master
    slot_dt_master = to_zone(slot_dt_utc, master.timezone)
    slot_master_str = slot_dt_master.strftime("%d.%m.%Y %H:%M")
    await callback.bot.send_message(
        chat_id=master.telegram_id,
        text=(
            "Новая запись на подтверждение 📩\n\n"
            f"<b>Клиент:</b> {client_name}\n"
            f"<b>Дата/время:</b> {slot_master_str}\n"
            f"<b>Длительность:</b> {master.slot_size_min} мин\n\n"
            "Подтвердить запись?"
        ),
        reply_markup=build_master_booking_review_keyboard(booking.id),
    )
