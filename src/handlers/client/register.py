import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import active_session, session_local
from src.handlers.client.client_menu import send_client_main_menu
from src.repositories import (
    ClientNotFound,
    ClientRepository,
    InviteNotFound,
    InviteRepository,
    MasterNotFound,
    MasterRepository,
)
from src.schemas import ClientCreate, Invite
from src.schemas.enums import Timezone
from src.utils import answer_tracked, cleanup_messages, track_callback_message, track_message, validate_phone

router = Router(name=__name__)
logger = logging.getLogger(__name__)

CLIENT_REGISTRATION_BUCKET = "client_registration"


class ClientRegistration(StatesGroup):
    name = State()
    phone = State()
    confirm = State()


def _build_confirm_registration_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Всё верно",
                    callback_data="client_reg_confirm",
                ),
                InlineKeyboardButton(
                    text="🔁 Заполнить заново",
                    callback_data="client_reg_restart",
                ),
            ],
        ],
    )


async def _get_valid_invite(invite_link: str, message: Message) -> Invite | None:
    token = invite_link.removeprefix("c_")
    async with active_session() as session:
        repo = InviteRepository(session)
        try:
            invite = await repo.get_by_token(token)
        except InviteNotFound:
            await message.answer(
                "Похоже, эта ссылка недействительна 😕\n"
                "Возможно, она устарела или была отправлена с ошибкой.\n\n"
                "Попроси мастера прислать новую ссылку ✨",
            )
            return None

        if not invite.is_invite_valid():
            await message.answer(
                "Эта ссылка на регистрацию больше не активна 😕\n"
                "Она могла истечь или быть использована ранее.\n\n"
                "Попроси мастера отправить новую ссылку ✨",
            )
            return None

        await repo.increment_used_count_if_valid(token)

        return invite


async def _check_if_master(telegram_id: int) -> bool:
    async with session_local() as session:
        repo = MasterRepository(session)
        try:
            await repo.get_by_telegram_id(telegram_id)
            return True
        except MasterNotFound:
            return False


async def _attach_client_if_exists(master_id: int, client_telegram_id: int) -> bool:
    async with active_session() as session:
        client_repo = ClientRepository(session)
        try:
            client = await client_repo.get_by_telegram_id(client_telegram_id)
            master_repo = MasterRepository(session)
            await master_repo.attach_client(master_id, client.id)
            return True
        except ClientNotFound:
            return False


async def _create_and_attach_client(
    client_create: ClientCreate,
    master_id: int,
) -> int:
    async with active_session() as session:
        repo = ClientRepository(session)
        client = await repo.create(client_create)
        master_repo = MasterRepository(session)
        await master_repo.attach_client(master_id, client.id)

    return client.id


async def start_client_registration(
    message: Message,
    state: FSMContext,
    invite_link: str,
) -> None:
    invite = await _get_valid_invite(invite_link, message)
    if invite is None:
        return

    telegram_id = message.from_user.id

    if await _attach_client_if_exists(invite.master_id, telegram_id):
        is_master = await _check_if_master(telegram_id)
        await send_client_main_menu(message, show_switch_role=is_master)
        return

    await state.update_data(invite_master_id=invite.master_id)
    await process_name_question(message, state)


async def process_name_question(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        text="Привет! 👋\n"
             "Давай настроим твой профиль клиента в BeautyDesk.\n\n"
             "Как тебя зовут? (Например: Маша)",
        bucket=CLIENT_REGISTRATION_BUCKET,
    )
    await state.set_state(ClientRegistration.name)


@router.message(StateFilter(ClientRegistration.name))
async def process_client_name(message: Message, state: FSMContext) -> None:
    await track_message(state, message, bucket=CLIENT_REGISTRATION_BUCKET)
    name = (message.text or "").strip()
    if not name:
        await answer_tracked(
            message,
            state,
            text="Я не понял имя 🤔\n"
                 "Пожалуйста, напиши, как к тебе обращаться. Например: <b>Маша</b>",
            bucket=CLIENT_REGISTRATION_BUCKET,
        )
        return

    await state.update_data(name=name)

    await answer_tracked(
        message,
        state,
        text=f"Отлично, <b>{name}</b>! ✨\n\n"
             "Добавь свой номер телефона (375...):",
        bucket=CLIENT_REGISTRATION_BUCKET,
    )
    await state.set_state(ClientRegistration.phone)


@router.message(StateFilter(ClientRegistration.phone))
async def process_client_phone(message: Message, state: FSMContext) -> None:
    await track_message(state, message, bucket=CLIENT_REGISTRATION_BUCKET)
    raw_text = (message.text or "").strip()
    phone = validate_phone(raw_text)
    if phone is None:
        await answer_tracked(
            message,
            state,
            text="Не смог разобрать телефонный номер 🤔\n\n"
                 "Пожалуйста, введи реальный номер в формате 375291234567, "
                 "чтобы мастер мог с тобой связаться:",
            bucket=CLIENT_REGISTRATION_BUCKET,
        )
        return

    await state.update_data(phone=phone)

    data = await state.get_data()

    text = (
        "Проверь, пожалуйста, данные 👇\n\n"
        f"<b>Имя:</b> {data['name']}\n"
        f"<b>Номер телефона:</b> {data['phone']}\n"
        "Всё верно?"
    )

    await answer_tracked(
        message,
        state,
        text=text,
        reply_markup=_build_confirm_registration_keyboard(),
        bucket=CLIENT_REGISTRATION_BUCKET,
    )
    await state.set_state(ClientRegistration.confirm)


@router.callback_query(
    StateFilter(ClientRegistration.confirm),
    F.data == "client_reg_confirm",
)
async def client_reg_confirm(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()
    await track_callback_message(state, callback, bucket=CLIENT_REGISTRATION_BUCKET)
    await answer_tracked(
        callback.message,
        state,
        text="⏳ Создаю профиль клиента…\n"
             "Пожалуйста, подожди несколько секунд.",
        bucket=CLIENT_REGISTRATION_BUCKET,
    )

    data = await state.get_data()
    invite_master_id = data.get("invite_master_id")
    telegram_id = callback.from_user.id

    client_create = ClientCreate(
        telegram_id=telegram_id,
        name=data["name"],
        phone=data["phone"],
        timezone=Timezone.EUROPE_MINSK,
    )

    client_id = await _create_and_attach_client(client_create, invite_master_id)
    logger.info(
        "client.created",
        extra={
            "master_id": invite_master_id, "client_id": client_id, "telegram_id": telegram_id,
        },
    )

    await callback.message.answer(
        "Готово! 🎉\n\n"
        "Твой профиль клиента создан.\n"
        "Теперь ты можешь вести записи в BeautyDesk.",
    )

    await cleanup_messages(state, callback.bot, bucket=CLIENT_REGISTRATION_BUCKET)
    await state.clear()
    is_master = await _check_if_master(telegram_id)

    await send_client_main_menu(callback.message, show_switch_role=is_master)


@router.callback_query(
    StateFilter(ClientRegistration.confirm),
    F.data == "client_reg_restart",
)
async def client_reg_restart(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await track_callback_message(state, callback, bucket=CLIENT_REGISTRATION_BUCKET)

    data = await state.get_data()
    invite_master_id = data.get("invite_master_id")

    await cleanup_messages(state, callback.bot, bucket=CLIENT_REGISTRATION_BUCKET)
    await state.clear()
    await state.update_data(invite_master_id=invite_master_id)

    await process_name_question(callback.message, state)
