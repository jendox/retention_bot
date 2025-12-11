import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import session_local
from src.handlers.client.client_menu import send_client_main_menu
from src.repositories import ClientRepository, InviteRepository, InviteNotFound, ClientNotFound, MasterRepository
from src.schemas import ClientCreate, Invite
from src.schemas.enums import Timezone
from src.utils import answer_tracked, cleanup_messages, track_callback_message, track_message, validate_phone

router = Router(name=__name__)
logger = logging.getLogger(__name__)


class ClientRegistration(StatesGroup):
    name = State()
    phone = State()
    confirm = State()


async def get_valid_invite(invite_link: str, message: Message) -> Invite | None:
    token = invite_link.removeprefix("c_")
    async with session_local() as session:
        async with session.begin():
            repo = InviteRepository(session)
            try:
                invite = await  repo.get_by_token(token)
            except InviteNotFound:
                await message.answer(
                    "Похоже, эта ссылка недействительна 😕\n"
                    "Возможно, она устарела или была отправлена с ошибкой.\n\n"
                    "Попроси мастера прислать новую ссылку ✨"
                )
                return None

            if not invite.is_invite_valid():
                await message.answer(
                    "Эта ссылка на регистрацию больше не активна 😕\n"
                    "Она могла истечь или быть использована ранее.\n\n"
                    "Попроси мастера отправить новую ссылку ✨"
                )
                return None

            await repo.increment_used_count_if_valid(token)

            return invite


async def start_client_registration(
    message: Message,
    state: FSMContext,
    invite_link: str,
) -> None:
    invite = await get_valid_invite(invite_link, message)
    if invite is None:
        return

    telegram_id = message.from_user.id
    async with session_local() as session:
        async with session.begin():
            client_repo = ClientRepository(session)
            try:
                client = await client_repo.get_by_telegram_id(telegram_id)
                master_repo = MasterRepository(session)
                await master_repo.attach_client(invite.master_id, client.id)
                await send_client_main_menu(message, state)
                return
            except ClientNotFound:
                pass

    await state.update_data(invite_master_id=invite.master_id)
    await process_name_question(message, state)


async def process_name_question(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        "Привет! 👋\n"
        "Давай настроим твой профиль в BeautyDesk.\n\n"
        "Как тебя зовут? (Например: Маша)",
    )
    await state.set_state(ClientRegistration.name)


@router.message(ClientRegistration.name)
async def process_client_name(message: Message, state: FSMContext) -> None:
    await track_message(state, message)
    name = (message.text or "").strip()
    if not name:
        await answer_tracked(
            message,
            state,
            text="Я не понял имя 🤔\n"
                 "Пожалуйста, напиши, как к тебе обращаться. Например: <b>Маша</b>",
        )
        return

    await state.update_data(name=name)

    await answer_tracked(
        message,
        state,
        text=f"Отлично, <b>{name}</b>! ✨\n\n"
             "Добавь свой номер телефона (375...):",
    )
    await state.set_state(ClientRegistration.phone)


@router.message(ClientRegistration.phone)
async def process_client_phone(message: Message, state: FSMContext) -> None:
    await track_message(state, message)
    raw_text = (message.text or "").strip()
    phone = validate_phone(raw_text)
    if phone is None:
        await answer_tracked(
            message,
            state,
            text="Не смог разобрать телефонный номер 🤔\n\n"
                 "Пожалуйста, введи реальный номер в формате 375291234567, "
                 "чтобы мастер мог с тобой связаться:",
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
    keyboard = InlineKeyboardMarkup(
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
    await answer_tracked(message, state, text=text, reply_markup=keyboard)
    await state.set_state(ClientRegistration.confirm)


@router.callback_query(
    ClientRegistration.confirm,
    F.data == "client_reg_confirm",
)
async def client_reg_confirm(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()
    await track_callback_message(state, callback)
    data = await state.get_data()
    telegram_id = callback.from_user.id

    client_create = ClientCreate(
        telegram_id=telegram_id,
        name=data["name"],
        phone=data["phone"],
        timezone=Timezone.EUROPE_MINSK,
    )

    async with session_local() as session:
        async with session.begin():
            repo = ClientRepository(session)
            client = await repo.create(client_create)
            invite_master_id = data.get("invite_master_id")
            if invite_master_id is not None:
                master_repo = MasterRepository(session)
                await master_repo.attach_client(invite_master_id, client.id)
            logger.info(
                "client.created",
                extra={
                    "master_id": invite_master_id, "client_id": client.id, "telegram_id": telegram_id
                },
            )

    await cleanup_messages(state, callback.bot)
    await state.clear()

    await callback.message.answer(
        "Готово! 🎉\n\n"
        "Твой профиль клиента создан.\n"
        "Теперь ты можешь вести записи в BeautyDesk.",
    )
    await send_client_main_menu(callback.message, state)


@router.callback_query(
    ClientRegistration.confirm,
    F.data == "client_reg_restart",
)
async def client_reg_restart(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()

    data = await state.get_data()
    invite_master_id = data.get("invite_master_id")

    await cleanup_messages(state, callback.bot)
    await state.clear()

    if invite_master_id is not None:
        await state.update_data(invite_master_id=invite_master_id)

    await process_name_question(callback.message, state)
