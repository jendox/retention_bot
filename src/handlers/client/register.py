import logging

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from pydantic import BaseModel, ValidationError

from src.core.sa import active_session, session_local
from src.handlers.client.client_menu import send_client_main_menu
from src.notifications import NotificationService, NotificationEvent, RecipientKind
from src.notifications.context import LimitsContext
from src.plans import FREE_CLIENTS_LIMIT
from src.repositories import (
    MasterNotFound,
    MasterRepository,
)
from src.use_cases.accept_client_invite import AcceptClientInvite, AcceptClientInviteRequest, AcceptInviteError
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
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="client_reg_cancel",
                ),
            ],
        ],
    )


def _build_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="client_reg_cancel")],
        ],
    )


async def _check_if_master(telegram_id: int) -> bool:
    async with session_local() as session:
        repo = MasterRepository(session)
        try:
            await repo.get_by_telegram_id(telegram_id)
            return True
        except MasterNotFound:
            return False


async def _send_error_message(
    bot: Bot,
    chat_id: int,
    error: AcceptInviteError | None,
) -> None:
    if error is None:
        return

    if error in {AcceptInviteError.INVITE_INVALID, AcceptInviteError.INVITE_NOT_FOUND}:
        await bot.send_message(
            chat_id,
            "Эта ссылка на регистрацию больше не активна 😕\n"
            "Она могла истечь или быть использована ранее.\n\n"
            "Попроси мастера отправить новую ссылку ✨",
        )
        return

    if error == AcceptInviteError.QUOTA_EXCEEDED:
        await bot.send_message(
            chat_id,
            "Похоже, у мастера закончился лимит клиентов на Free 😕\n\n"
            "Попроси мастера подключить Pro или прислать ссылку позже.",
        )
        return

    if error == AcceptInviteError.PHONE_CONFLICT:
        await bot.send_message(
            chat_id,
            "Не получилось подключиться по ссылке 😕\n"
            "Похоже, у мастера уже есть клиент с таким телефоном.\n\n"
            "Попроси мастера помочь тебе подключиться.",
        )
        return


class InviteData(BaseModel):
    invite_master_id: int
    invite_token: str


async def start_client_registration(
    message: Message,
    state: FSMContext,
    invite_link: str,
) -> None:
    await track_message(state, message, bucket=CLIENT_REGISTRATION_BUCKET)
    token = invite_link.removeprefix("c_")
    telegram_id = message.from_user.id
    async with active_session() as session:
        result = await AcceptClientInvite(session).execute(
            AcceptClientInviteRequest(
                telegram_id=telegram_id,
                invite_token=token,
            ),
        )

    if result.ok:
        is_master = await _check_if_master(telegram_id)
        await send_client_main_menu(message.bot, telegram_id, show_switch_role=is_master)
        return

    # Continue with FSM
    if result.error == AcceptInviteError.MISSING_PHONE:
        invite_data = InviteData(invite_master_id=result.master_id, invite_token=token)
        await state.update_data(invite_data=invite_data.model_dump())
        await process_name_question(message, state)
        return

    await _send_error_message(message.bot, telegram_id, result.error)

    await cleanup_messages(state, message.bot, bucket=CLIENT_REGISTRATION_BUCKET)


async def process_name_question(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        text="Привет! 👋\n"
             "Давай настроим твой профиль клиента в BeautyDesk.\n\n"
             "Как тебя зовут? (Например: Маша)",
        bucket=CLIENT_REGISTRATION_BUCKET,
        reply_markup=_build_cancel_keyboard(),
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
            reply_markup=_build_cancel_keyboard(),
        )
        return

    await state.update_data(name=name)

    await answer_tracked(
        message,
        state,
        text=f"Отлично, <b>{name}</b>! ✨\n\n"
             "Добавь свой номер телефона (375...):",
        bucket=CLIENT_REGISTRATION_BUCKET,
        reply_markup=_build_cancel_keyboard(),
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
            reply_markup=_build_cancel_keyboard(),
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
    telegram_id = callback.from_user.id

    try:
        invite_data = InviteData.model_validate(data.get("invite_data"))
    except ValidationError:
        await callback.answer(text="Что-то пошло не так, попробуй ещё раз", show_alert=True)
        await cleanup_messages(state, callback.bot, bucket=CLIENT_REGISTRATION_BUCKET)
        await state.clear()
        return

    phone = data["phone"]
    name = data["name"]

    async with active_session() as session:
        result = await AcceptClientInvite(session).execute(
            AcceptClientInviteRequest(
                telegram_id=telegram_id,
                invite_token=invite_data.invite_token,
                name=name,
                phone_e164=phone,
            ),
        )

    bot = callback.bot
    await cleanup_messages(state, callback.bot, bucket=CLIENT_REGISTRATION_BUCKET)
    await state.clear()

    if not result.ok:
        logger.error(
            "client.registration_failed",
            extra={"telegram_id": telegram_id, "master_id": result.master_id, "error": result.error.value},
        )
        await _send_error_message(bot, telegram_id, result.error)
        return

    logger.info(
        "client.registration_success",
        extra={"master_id": result.master_id, "client_id": result.client_id, "telegram_id": telegram_id},
    )
    await bot.send_message(
        chat_id=telegram_id,
        text="Готово! 🎉\n\n"
             "Твой профиль клиента создан.\n"
             "Теперь ты можешь управлять записями в BeautyDesk.",
    )

    if result.warn_master_clients_near_limit:
        notification = NotificationService(bot)
        await notification.send_limits(
            event=NotificationEvent.WARNING_NEAR_CLIENTS_LIMIT,
            recipient=RecipientKind.MASTER,
            chat_id=result.master_telegram_id,
            context=LimitsContext(
                usage=result.usage,
                clients_limit=FREE_CLIENTS_LIMIT,
            ),
        )

    is_master = await _check_if_master(telegram_id)
    await send_client_main_menu(bot, telegram_id, show_switch_role=is_master)


@router.callback_query(
    StateFilter(ClientRegistration.confirm),
    F.data == "client_reg_restart",
)
async def client_reg_restart(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await track_callback_message(state, callback, bucket=CLIENT_REGISTRATION_BUCKET)

    data = await state.get_data()
    invite_data = InviteData.model_validate(data.get("invite_data"))

    await cleanup_messages(state, callback.bot, bucket=CLIENT_REGISTRATION_BUCKET)
    await state.clear()
    await state.update_data(invite_data=invite_data.model_dump())

    await process_name_question(callback.message, state)


@router.callback_query(
    StateFilter(ClientRegistration.name, ClientRegistration.phone, ClientRegistration.confirm),
    F.data == "client_reg_cancel",
)
async def client_reg_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Окей, отменил.", show_alert=True)
    await cleanup_messages(state, callback.bot, bucket=CLIENT_REGISTRATION_BUCKET)
    await state.clear()
