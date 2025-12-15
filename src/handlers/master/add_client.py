import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import active_session, session_local
from src.repositories import ClientNotFound, ClientRepository, MasterRepository
from src.schemas import ClientCreate
from src.use_cases.entitlements import EntitlementsService
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
            [
                InlineKeyboardButton(text="❌ Отмена", callback_data="master_add_client_cancel"),
            ],
        ],
    )


def _build_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="master_add_client_cancel")]],
    )


async def _create_client(master_id: int, name: str, phone: str) -> int | None:
    async with active_session() as session:
        client_repo = ClientRepository(session)
        try:
            existing = await client_repo.find_for_master_by_phone(master_id=master_id, phone=phone)
        except ClientNotFound:
            existing = None
        if existing is not None:
            logger.info(
                "master.add_client.duplicate_phone",
                extra={"master_id": master_id, "client_id": existing.id, "phone": phone},
            )
            return None
        client = await client_repo.create(
            ClientCreate(
                telegram_id=None,
                name=name,
                phone=phone,
            ),
        )
        master_repo = MasterRepository(session)
        await master_repo.attach_client(master_id, client.id)
        logger.info(
            "master.add_client.created",
            extra={"master_id": master_id, "client_id": client.id, "phone": phone},
        )
        return client.id


async def start_add_client(callback: CallbackQuery, state: FSMContext) -> None:
    logger.info(
        "master.add_client.start",
        extra={"telegram_id": callback.from_user.id if callback.from_user else None},
    )
    async with session_local() as session:
        master_repo = MasterRepository(session)
        master = await master_repo.get_by_telegram_id(callback.from_user.id)
        entitlements = EntitlementsService(session)
        check = await entitlements.can_attach_client(master_id=master.id)

    if not check.allowed:
        await answer_tracked(
            callback.message,
            state,
            text=(
                "Похоже, у тебя закончился лимит клиентов на Free.\n\n"
                f"<b>Клиенты:</b> {check.current}/{check.limit}\n\n"
                "Чтобы добавить больше клиентов — подключи Pro."
            ),
            bucket=ADD_CLIENT_BUCKET,
        )
        return

    warning = ""
    if check.limit is not None and check.current >= int(check.limit * 0.8):  # noqa: PLR2004
        warning = (
            "\n\n⚠️ Лимит клиентов на Free почти исчерпан:\n"
            f"<b>{check.current}</b> из <b>{check.limit}</b>.\n"
            "В Pro лимитов нет."
        )

    await answer_tracked(
        callback.message,
        state,
        text="Добавим клиента ✍️\n\nКак зовут клиента?"
             f"{warning}",
        bucket=ADD_CLIENT_BUCKET,
        reply_markup=_build_cancel_keyboard(),
    )
    await state.set_state(AddClientStates.name)


@router.message(StateFilter(AddClientStates.name))
async def process_client_name(message: Message, state: FSMContext) -> None:
    await track_message(state, message, bucket=ADD_CLIENT_BUCKET)
    name = (message.text or "").strip()
    if not name:
        logger.debug(
            "master.add_client.invalid_name",
            extra={"telegram_id": message.from_user.id if message.from_user else None},
        )
        await answer_tracked(
            message,
            state,
            text="Имя не понял 😅 Введи, пожалуйста, имя клиента.",
            bucket=ADD_CLIENT_BUCKET,
            reply_markup=_build_cancel_keyboard(),
        )
        return

    await state.update_data(name=name)
    await answer_tracked(
        message,
        state,
        text="Записал. Теперь номер телефона (для связи):",
        bucket=ADD_CLIENT_BUCKET,
        reply_markup=_build_cancel_keyboard(),
    )
    await state.set_state(AddClientStates.phone)


@router.message(StateFilter(AddClientStates.phone))
async def process_client_phone(message: Message, state: FSMContext) -> None:
    await track_message(state, message, bucket=ADD_CLIENT_BUCKET)
    raw_phone = (message.text or "").strip()
    phone = validate_phone(raw_phone)
    if phone is None:
        logger.debug(
            "master.add_client.invalid_phone",
            extra={
                "telegram_id": message.from_user.id if message.from_user else None,
                "raw_phone": raw_phone,
            },
        )
        await answer_tracked(
            message,
            state,
            text="Нужен реальный номер в формате 375291234567, чтобы связаться с клиентом.",
            bucket=ADD_CLIENT_BUCKET,
            reply_markup=_build_cancel_keyboard(),
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
    await start_add_client(callback, state)


@router.callback_query(StateFilter(AddClientStates.confirm), F.data == "master_add_client_confirm")
async def master_add_client_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    logger.info(
        "master.add_client.confirm",
        extra={"telegram_id": callback.from_user.id if callback.from_user else None},
    )
    await track_callback_message(state, callback, bucket=ADD_CLIENT_BUCKET)

    data = await state.get_data()
    name = data.get("name")
    phone = data.get("phone")
    if not name or not phone:
        logger.warning(
            "master.add_client.missing_data",
            extra={
                "telegram_id": callback.from_user.id if callback.from_user else None,
                "name": bool(name),
                "phone": bool(phone),
            },
        )
        await callback.answer("Не хватает данных, попробуйте заново", show_alert=True)
        await cleanup_messages(state, callback.bot, bucket=ADD_CLIENT_BUCKET)
        await state.clear()
        return

    async with session_local() as session:
        master_repo = MasterRepository(session)
        master = await master_repo.get_by_telegram_id(callback.from_user.id)

    created_client_id = await _create_client(master.id, name, phone)
    if created_client_id is None:
        text = "ℹ️ Клиент с таким телефоном уже есть в твоей базе."
    else:
        text = "✅ Готово! Клиент добавлен (🔴 оффлайн)"
    await callback.answer(text=text, show_alert=True)

    await cleanup_messages(state, callback.bot, bucket=ADD_CLIENT_BUCKET)
    await state.clear()


@router.callback_query(
    StateFilter(AddClientStates.name, AddClientStates.phone, AddClientStates.confirm),
    F.data == "master_add_client_cancel",
)
async def master_add_client_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Окей, отменил.", show_alert=True)
    await cleanup_messages(state, callback.bot, bucket=ADD_CLIENT_BUCKET)
    await state.clear()
