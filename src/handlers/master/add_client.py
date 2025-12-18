import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import active_session
from src.filters.user_role import UserRole
from src.notifications import NotificationEvent, RecipientKind
from src.notifications.context import LimitsContext
from src.notifications.notifier import NotificationRequest, Notifier
from src.notifications.policy import NotificationFacts
from src.use_cases.create_client_offline import CreateClientOffline, CreateClientOfflineError
from src.use_cases.entitlements import Usage
from src.user_context import ActiveRole
from src.utils import answer_tracked, cleanup_messages, track_callback_message, track_message, validate_phone

logger = logging.getLogger(__name__)
router = Router(name=__name__)

ADD_CLIENT_BUCKET = "master_add_client"

CLIENT_ADD_CB = {
    "confirm": "m:add_client:confirm",
    "restart": "m:add_client:restart",
    "cancel": "m:add_client:cancel",
}

ERROR_MESSAGE: dict[CreateClientOfflineError | None, str] = {
    CreateClientOfflineError.MASTER_NOT_FOUND: "⚠️ Профиль мастера не найден. Пройдите регистрацию.",
    CreateClientOfflineError.INVALID_REQUEST: "❌ Возникла ошибка. Попробуйте ещё раз.",
    CreateClientOfflineError.PHONE_CONFLICT: "ℹ️ Клиент с таким телефоном уже есть в вашей базе.\n"
                                             "Проверьте правильность введённого номера",
    None: "Неизвестная ошибка.",
}


class AddClientStates(StatesGroup):
    name = State()
    phone = State()
    confirm = State()


def _build_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Всё верно", callback_data=CLIENT_ADD_CB["confirm"]),
                InlineKeyboardButton(text="🔁 Заполнить заново", callback_data=CLIENT_ADD_CB["restart"]),
            ],
            [
                InlineKeyboardButton(text="❌ Отмена", callback_data=CLIENT_ADD_CB["cancel"]),
            ],
        ],
    )


def _build_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="❌ Отмена", callback_data=CLIENT_ADD_CB["cancel"]),
            ],
        ],
    )


async def _send_warning_message(
    *,
    chat_id: int,
    event: NotificationEvent,
    usage: Usage | None,
    plan_is_pro: bool | None,
    clients_limit: int | None,
    notifier: Notifier,
) -> bool:
    if clients_limit is None or usage is None:
        return False

    request = NotificationRequest(
        chat_id=chat_id,
        event=event,
        recipient=RecipientKind.MASTER,
        context=LimitsContext(usage=usage, clients_limit=clients_limit),
        facts=NotificationFacts(
            event=event,
            recipient=RecipientKind.MASTER,
            chat_id=chat_id,
            plan_is_pro=plan_is_pro,
        ),
    )
    return await notifier.maybe_send(request)


async def start_add_client(
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
) -> None:
    telegram_id = callback.from_user.id
    await track_callback_message(state, callback, bucket=ADD_CLIENT_BUCKET)
    logger.debug("master.add_client_offline.start", extra={"telegram_id": telegram_id})

    async with active_session() as session:
        result = await CreateClientOffline(session).preflight(telegram_master_id=telegram_id)

    if not result.ok:
        logger.error(
            "master.add_client_offline.failed",
            extra={"telegram_id": telegram_id, "error": result.error_detail},
        )
        await callback.answer(ERROR_MESSAGE.get(result.error, "Неожиданная ошибка."), show_alert=True)
        await cleanup_messages(state, callback.bot, bucket=ADD_CLIENT_BUCKET)
        await state.clear()
        return

    if not result.allowed:
        assert result.usage is not None
        logger.warning(
            "master.add_client_offline.limit_clients_reached",
            extra={
                "telegram_id": telegram_id,
                "clients_count": result.usage.clients_count,
                "clients_limit": result.clients_limit,
                "error": "quota_exceeded",
            },
        )
        if not await _send_warning_message(
            chat_id=telegram_id,
            event=NotificationEvent.LIMIT_CLIENTS_REACHED,
            usage=result.usage,
            plan_is_pro=result.plan_is_pro,
            clients_limit=result.clients_limit,
            notifier=notifier,
        ):
            await callback.answer("Лимит клиентов исчерпан.", show_alert=True)

        await cleanup_messages(state, callback.bot, bucket=ADD_CLIENT_BUCKET)
        await state.clear()
        return

    await answer_tracked(
        callback.message,
        state,
        text="Добавим клиента ✍️\n\nКак зовут клиента?",
        bucket=ADD_CLIENT_BUCKET,
        reply_markup=_build_cancel_keyboard(),
    )
    await state.set_state(AddClientStates.name)


@router.message(UserRole(ActiveRole.MASTER), StateFilter(AddClientStates.name))
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


@router.message(UserRole(ActiveRole.MASTER), StateFilter(AddClientStates.phone))
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


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(AddClientStates.confirm),
    F.data == CLIENT_ADD_CB["restart"],
)
async def master_add_client_restart(
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
) -> None:
    await callback.answer()
    await track_callback_message(state, callback, bucket=ADD_CLIENT_BUCKET)
    await cleanup_messages(state, callback.bot, bucket=ADD_CLIENT_BUCKET)
    await state.clear()
    await start_add_client(callback, state, notifier)


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(AddClientStates.name, AddClientStates.phone, AddClientStates.confirm),
    F.data == CLIENT_ADD_CB["cancel"],
)
async def master_add_client_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    logger.debug(
        "master.add_client_offline.cancelled",
        extra={"telegram_id": callback.from_user.id},
    )
    await callback.answer("Добавление клиента отменено.", show_alert=True)
    await cleanup_messages(state, callback.bot, bucket=ADD_CLIENT_BUCKET)
    await state.clear()


@router.callback_query(
    UserRole(ActiveRole.MASTER),
    StateFilter(AddClientStates.confirm),
    F.data == CLIENT_ADD_CB["confirm"],
)
async def master_add_client_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
) -> None:
    telegram_id = callback.from_user.id
    await track_callback_message(state, callback, bucket=ADD_CLIENT_BUCKET)

    data = await state.get_data()
    name = data.get("name")
    phone = data.get("phone")
    if not name or not phone:
        logger.warning(
            "master.add_client_offline.missing_data",
            extra={
                "telegram_id": telegram_id,
                "name": bool(name),
                "phone": bool(phone),
            },
        )
        await callback.answer("Не хватает данных, попробуйте заново.", show_alert=True)
        await cleanup_messages(state, callback.bot, bucket=ADD_CLIENT_BUCKET)
        await state.clear()
        return

    async with active_session() as session:
        result = await CreateClientOffline(session).create(
            telegram_master_id=telegram_id,
            phone_e164=phone,
            name=name,
        )

    if result.ok:
        logger.info(
            "master.add_client_offline.success",
            extra={"master_id": result.master_id, "client_id": result.client_id},
        )
        await callback.answer("✅ Готово! Клиент добавлен (🔴 оффлайн)", show_alert=True)
        await cleanup_messages(state, callback.bot, bucket=ADD_CLIENT_BUCKET)
        await state.clear()

        if result.warn_master_clients_near_limit:
            await _send_warning_message(
                chat_id=telegram_id,
                event=NotificationEvent.WARNING_NEAR_CLIENTS_LIMIT,
                usage=result.usage,
                plan_is_pro=result.plan_is_pro,
                clients_limit=result.clients_limit,
                notifier=notifier,
            )
        return

    if result.error == CreateClientOfflineError.PHONE_CONFLICT:
        logger.warning(
            "master.add_client_offline.conflict",
            extra={"telegram_id": telegram_id, "conflict_phone": phone},
        )
        await callback.answer(text=ERROR_MESSAGE.get(result.error, ERROR_MESSAGE[None]), show_alert=True)
        await answer_tracked(
            callback.message,
            state,
            text="Укажите номер телефона:",
            bucket=ADD_CLIENT_BUCKET,
            reply_markup=_build_cancel_keyboard(),
        )
        await state.set_state(AddClientStates.phone)
        return

    if result.error == CreateClientOfflineError.MASTER_NOT_FOUND:
        logger.warning(
            "master.add_client_offline.master_not_found",
            extra={"telegram_id": telegram_id},
        )
        await callback.answer(text=ERROR_MESSAGE.get(result.error, ERROR_MESSAGE[None]), show_alert=True)
    elif result.error == CreateClientOfflineError.QUOTA_EXCEEDED:
        logger.warning(
            "master.add_client_offline.limit_clients_reached",
            extra={
                "telegram_id": telegram_id,
                "clients_count": result.usage.clients_count if getattr(result, "usage", None) else None,
                "clients_limit": result.clients_limit,
                "error": "quota_exceeded",
            },
        )
        if not await _send_warning_message(
            chat_id=telegram_id,
            event=NotificationEvent.LIMIT_CLIENTS_REACHED,
            usage=result.usage,
            plan_is_pro=result.plan_is_pro,
            clients_limit=result.clients_limit,
            notifier=notifier,
        ):
            await callback.answer("Лимит клиентов исчерпан.", show_alert=True)
    else:
        logger.warning(
            "master.add_client_offline.invalid_request",
            extra={"telegram_id": telegram_id, "error": str(result.error)},
        )
        await callback.answer(
            text=ERROR_MESSAGE.get(result.error, ERROR_MESSAGE[None]), show_alert=True)

    await cleanup_messages(state, callback.bot, bucket=ADD_CLIENT_BUCKET)
    await state.clear()
