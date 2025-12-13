import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import active_session, session_local
from src.handlers.master.master_menu import send_master_main_menu
from src.repositories import ClientRepository, MasterRepository
from src.schemas import ClientCreate
from src.utils import answer_tracked, cleanup_messages, track_callback_message, track_message, validate_phone

logger = logging.getLogger(__name__)
router = Router(name=__name__)

ADD_CLIENT_BUCKET = "master_add_client"


class AddClientStates(StatesGroup):
    name = State()
    phone = State()
    confirm = State()


def _build_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Всё верно", callback_data="master_add_client_confirm"),
                InlineKeyboardButton(text="🔁 Заполнить заново", callback_data="master_add_client_restart"),
            ],
        ],
    )


async def _create_client(master_id: int, name: str, phone: str) -> int:
    async with active_session() as session:
        client_repo = ClientRepository(session)
        client = await client_repo.create(
            ClientCreate(
                telegram_id=None,
                name=name,
                phone=phone,
            ),
        )
        master_repo = MasterRepository(session)
        await master_repo.attach_client(master_id, client.id)
        return client.id


async def start_add_client(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        text="Добавим клиента ✍️\n\nКак зовут клиента?",
        bucket=ADD_CLIENT_BUCKET,
    )
    await state.set_state(AddClientStates.name)


@router.message(StateFilter(AddClientStates.name))
async def process_client_name(message: Message, state: FSMContext) -> None:
    await track_message(state, message, bucket=ADD_CLIENT_BUCKET)
    name = (message.text or "").strip()
    if not name:
        await answer_tracked(
            message,
            state,
            text="Имя не понял 😅 Введи, пожалуйста, имя клиента.",
            bucket=ADD_CLIENT_BUCKET,
        )
        return

    await state.update_data(name=name)
    await answer_tracked(
        message,
        state,
        text="Записал. Теперь номер телефона (для связи):",
        bucket=ADD_CLIENT_BUCKET,
    )
    await state.set_state(AddClientStates.phone)


@router.message(StateFilter(AddClientStates.phone))
async def process_client_phone(message: Message, state: FSMContext) -> None:
    await track_message(state, message, bucket=ADD_CLIENT_BUCKET)
    raw_phone = (message.text or "").strip()
    phone = validate_phone(raw_phone)
    if phone is None:
        await answer_tracked(
            message,
            state,
            text="Нужен реальный номер в формате 375291234567, чтобы связаться с клиентом.",
            bucket=ADD_CLIENT_BUCKET,
        )
        return

    await state.update_data(phone=phone)
    data = await state.get_data()
    text = (
        "Проверь, пожалуйста:\n\n"
        f"<b>Имя:</b> {data['name']}\n"
        f"<b>Телефон:</b> {phone}\n"
        "Всё верно?"
    )
    await answer_tracked(
        message,
        state,
        text=text,
        reply_markup=_build_confirm_keyboard(),
        bucket=ADD_CLIENT_BUCKET,
    )
    await state.set_state(AddClientStates.confirm)


@router.callback_query(StateFilter(AddClientStates.confirm), F.data == "master_add_client_restart")
async def master_add_client_restart(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await track_callback_message(state, callback, bucket=ADD_CLIENT_BUCKET)
    await cleanup_messages(state, callback.bot, bucket=ADD_CLIENT_BUCKET)
    await state.clear()
    await start_add_client(callback.message, state)


@router.callback_query(StateFilter(AddClientStates.confirm), F.data == "master_add_client_confirm")
async def master_add_client_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    await track_callback_message(state, callback, bucket=ADD_CLIENT_BUCKET)

    data = await state.get_data()
    name = data.get("name")
    phone = data.get("phone")
    if not name or not phone:
        await callback.answer("Не хватает данных, попробуйте заново", show_alert=True)
        await cleanup_messages(state, callback.bot, bucket=ADD_CLIENT_BUCKET)
        await state.clear()
        return

    await callback.answer()

    async with session_local() as session:
        master_repo = MasterRepository(session)
        master = await master_repo.get_by_telegram_id(callback.from_user.id)

    client_id = await _create_client(master.id, name, phone)

    await callback.message.answer("Готово! Клиент добавлен (офлайн) ✅")

    await cleanup_messages(state, callback.bot, bucket=ADD_CLIENT_BUCKET)
    await state.clear()
    await send_master_main_menu(callback.message, show_switch_role=True)
