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
from src.schemas import ClientCreate, ClientUpdate, Invite
from src.schemas.enums import Timezone
from src.use_cases.entitlements import EntitlementsService
from src.plans import FREE_CLIENTS_LIMIT
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


async def _claim_offline_client_if_exists(
    *,
    master_id: int,
    phone: str,
    client_update: ClientUpdate,
) -> bool:
    async with active_session() as session:
        client_repo = ClientRepository(session)
        master_repo = MasterRepository(session)
        offline = await client_repo.find_offline_for_master_by_phone(master_id=master_id, phone=phone)
        if offline is None:
            return False
        # Safety: don't overwrite an existing TG binding (shouldn't happen due to filter),
        # but keep it explicit.
        if offline.telegram_id is not None and offline.telegram_id != client_update.telegram_id:
            return False

        await client_repo.update_by_id(offline.id, client_update)
        await master_repo.attach_client(master_id, offline.id)
        return True


async def start_client_registration(
    message: Message,
    state: FSMContext,
    invite_link: str,
) -> None:
    token = invite_link.removeprefix("c_")
    invite = await _get_valid_invite(invite_link, message)
    if invite is None:
        return

    telegram_id = message.from_user.id
    master_id = invite.master_id

    async with active_session() as session:
        client_repo = ClientRepository(session)
        invite_repo = InviteRepository(session)
        master_repo = MasterRepository(session)
        entitlements = EntitlementsService(session)
        try:
            client = await client_repo.get_by_telegram_id(telegram_id)
        except ClientNotFound:
            client = None

        if client is not None:
            already_attached = await master_repo.is_client_attached(master_id=master_id, client_id=client.id)
            if not already_attached:
                check = await entitlements.can_attach_client(master_id=master_id)
                if not check.allowed:
                    await message.answer(
                        "Похоже, у мастера закончился лимит клиентов на Free 😕\n\n"
                        "Попроси мастера подключить Pro или прислать ссылку позже.",
                    )
                    return

            consumed = await invite_repo.increment_used_count_if_valid(token)
            if not consumed:
                await message.answer(
                    "Эта ссылка на регистрацию больше не активна 😕\n"
                    "Она могла истечь или быть использована ранее.\n\n"
                    "Попроси мастера отправить новую ссылку ✨",
                )
                return

            await master_repo.attach_client(master_id, client.id)

            is_master = await _check_if_master(telegram_id)
            await send_client_main_menu(message, show_switch_role=is_master)
            return

    await state.update_data(invite_master_id=master_id, invite_token=token)
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
    invite_token = data.get("invite_token")
    telegram_id = callback.from_user.id

    if invite_master_id is None or not invite_token:
        await callback.answer("Что-то пошло не так, попробуй ещё раз", show_alert=True)
        return

    phone = data["phone"]
    name = data["name"]

    created_client_id: int | None = None
    master_telegram_id: int | None = None
    warn_clients: tuple[int, int] | None = None

    async with active_session() as session:
        invite_repo = InviteRepository(session)
        client_repo = ClientRepository(session)
        master_repo = MasterRepository(session)
        entitlements = EntitlementsService(session)

        try:
            invite = await invite_repo.get_by_token(invite_token)
        except InviteNotFound:
            await callback.message.answer(
                "Похоже, эта ссылка недействительна 😕\n"
                "Попроси мастера прислать новую ссылку ✨",
            )
            return

        if not invite.is_invite_valid():
            await callback.message.answer(
                "Эта ссылка на регистрацию больше не активна 😕\n"
                "Попроси мастера отправить новую ссылку ✨",
            )
            return

        master = await master_repo.get_by_id(invite_master_id)
        master_telegram_id = master.telegram_id

        offline_client = None
        try:
            offline_client = await client_repo.find_offline_for_master_by_phone(
                master_id=invite_master_id,
                phone=phone,
            )
        except ClientNotFound:
            offline_client = None

        existing_client = None
        if offline_client is None:
            try:
                existing_client = await client_repo.get_by_telegram_id(telegram_id)
            except ClientNotFound:
                existing_client = None

        needs_quota = True
        if offline_client is not None:
            needs_quota = False
        elif existing_client is not None:
            already_attached = await master_repo.is_client_attached(
                master_id=invite_master_id,
                client_id=existing_client.id,
            )
            needs_quota = not already_attached

        if needs_quota:
            check = await entitlements.can_attach_client(master_id=invite_master_id)
            if not check.allowed:
                await callback.message.answer(
                    "Похоже, у мастера закончился лимит клиентов на Free 😕\n\n"
                    "Попроси мастера подключить Pro или повтори попытку позже.",
                )
                return

        consumed = await invite_repo.increment_used_count_if_valid(invite_token)
        if not consumed:
            await callback.message.answer(
                "Эта ссылка на регистрацию больше не активна 😕\n"
                "Попроси мастера отправить новую ссылку ✨",
            )
            return

        if offline_client is not None:
            if offline_client.telegram_id is not None and offline_client.telegram_id != telegram_id:
                await callback.message.answer(
                    "Не получилось завершить регистрацию 😕\n"
                    "Попроси мастера помочь тебе подключиться.",
                )
                return
            await client_repo.update_by_id(
                offline_client.id,
                ClientUpdate(
                    telegram_id=telegram_id,
                    name=name,
                    timezone=Timezone.EUROPE_MINSK,
                ),
            )
            await master_repo.attach_client(invite_master_id, offline_client.id)
            created_client_id = offline_client.id
        elif existing_client is not None:
            await master_repo.attach_client(invite_master_id, existing_client.id)
            created_client_id = existing_client.id
        else:
            client = await client_repo.create(
                ClientCreate(
                    telegram_id=telegram_id,
                    name=name,
                    phone=phone,
                    timezone=Timezone.EUROPE_MINSK,
                ),
            )
            await master_repo.attach_client(invite_master_id, client.id)
            created_client_id = client.id

        logger.info(
            "client.registered",
            extra={
                "master_id": invite_master_id,
                "client_id": created_client_id,
                "telegram_id": telegram_id,
            },
        )

        close = await entitlements.near_limits(master_id=invite_master_id, threshold=0.8)
        if "clients" in close:
            usage = await entitlements.get_usage(master_id=invite_master_id)
            warn_clients = (usage.clients_count, FREE_CLIENTS_LIMIT)

    await callback.message.answer(
        "Готово! 🎉\n\n"
        "Твой профиль клиента создан.\n"
        "Теперь ты можешь вести записи в BeautyDesk.",
    )

    await cleanup_messages(state, callback.bot, bucket=CLIENT_REGISTRATION_BUCKET)
    await state.clear()
    is_master = await _check_if_master(telegram_id)

    if master_telegram_id and warn_clients:
        current, limit = warn_clients
        try:
            await callback.bot.send_message(
                chat_id=master_telegram_id,
                text=(
                    "⚠️ Лимит клиентов на Free почти исчерпан.\n\n"
                    f"<b>{current}</b> из <b>{limit}</b> клиентов.\n"
                    "В Pro лимитов нет."
                ),
            )
        except Exception:
            logger.debug("client_reg.warn_master_failed", exc_info=True)

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
