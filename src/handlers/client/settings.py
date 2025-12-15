import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import active_session, session_local
from src.filters.user_role import UserRole
from src.repositories import ClientNotFound, ClientRepository
from src.schemas import ClientUpdate
from src.schemas.enums import Timezone
from src.user_context import ActiveRole

logger = logging.getLogger(__name__)
router = Router(name=__name__)

SETTINGS_CB_PREFIX = "c:settings:"
SETTINGS_MAIN_KEY = "client_settings_main"


def _kb_settings(*, notifications_enabled: bool) -> InlineKeyboardMarkup:
    notify_text = "🔔 Уведомления: включены ✅" if notifications_enabled else "🔕 Уведомления: выключены 🚫"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🌍 Таймзона", callback_data=f"{SETTINGS_CB_PREFIX}tz")],
            [InlineKeyboardButton(text=notify_text, callback_data=f"{SETTINGS_CB_PREFIX}toggle_notify")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"{SETTINGS_CB_PREFIX}back")],
        ],
    )


def _kb_timezones() -> InlineKeyboardMarkup:
    common = [
        Timezone.EUROPE_MINSK,
        Timezone.EUROPE_MOSCOW,
        Timezone.EUROPE_WARSAW,
        Timezone.EUROPE_VILNIUS,
        Timezone.EUROPE_RIGA,
        Timezone.EUROPE_TALLINN,
        Timezone.EUROPE_LONDON,
        Timezone.EUROPE_BERLIN,
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for tz in common:
        rows.append([InlineKeyboardButton(text=str(tz.value), callback_data=f"{SETTINGS_CB_PREFIX}set_tz:{tz.value}")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"{SETTINGS_CB_PREFIX}back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _render(*, name: str, tz: Timezone, notifications_enabled: bool) -> str:
    notify_line = "включены ✅" if notifications_enabled else "выключены 🚫"
    return (
        "Настройки клиента ⚙️\n\n"
        f"<b>Профиль:</b> {name}\n"
        f"<b>Таймзона:</b> {tz.value}\n"
        f"<b>Уведомления:</b> {notify_line}\n\n"
        "Что настроим?"
    )


async def open_client_settings(message: Message, state: FSMContext) -> None:
    telegram_id = message.from_user.id
    async with session_local() as session:
        repo = ClientRepository(session)
        try:
            client = await repo.get_by_telegram_id(telegram_id)
        except ClientNotFound:
            await message.answer("Команда доступна клиентам после регистрации по ссылке мастера.")
            return

    data = await state.get_data()
    main = data.get(SETTINGS_MAIN_KEY) or {}
    prev_chat_id = main.get("chat_id")
    prev_message_id = main.get("message_id")
    if prev_chat_id and prev_message_id:
        try:
            await message.bot.delete_message(chat_id=prev_chat_id, message_id=prev_message_id)
        except Exception:
            logger.debug("client.settings.delete_prev_failed", exc_info=True)

    settings_msg = await message.answer(
        text=_render(
            name=client.name,
            tz=client.timezone,
            notifications_enabled=getattr(client, "notifications_enabled", True),
        ),
        reply_markup=_kb_settings(notifications_enabled=getattr(client, "notifications_enabled", True)),
    )
    await state.update_data(
        **{SETTINGS_MAIN_KEY: {"chat_id": settings_msg.chat.id, "message_id": settings_msg.message_id}},
    )


async def _refresh_settings_message(*, state: FSMContext, bot, telegram_id: int) -> bool:
    data = await state.get_data()
    main = data.get(SETTINGS_MAIN_KEY) or {}
    chat_id = main.get("chat_id") or telegram_id
    message_id = main.get("message_id")
    if message_id is None:
        return False
    async with session_local() as session:
        repo = ClientRepository(session)
        client = await repo.get_by_telegram_id(telegram_id)
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=_render(
            name=client.name,
            tz=client.timezone,
            notifications_enabled=getattr(client, "notifications_enabled", True),
        ),
        reply_markup=_kb_settings(notifications_enabled=getattr(client, "notifications_enabled", True)),
        parse_mode="HTML",
    )
    return True


@router.callback_query(UserRole(ActiveRole.CLIENT), F.data.startswith(SETTINGS_CB_PREFIX))
async def settings_callbacks(callback: CallbackQuery, state: FSMContext) -> None:
    telegram_id = callback.from_user.id
    data = callback.data or ""

    if data == f"{SETTINGS_CB_PREFIX}back":
        await callback.answer()
        try:
            await callback.message.delete()
        except Exception:
            logger.debug("client.settings.delete_failed", exc_info=True)
        return

    # Ensure the main settings message is tracked for single-screen UX.
    data_ = await state.get_data()
    main = data_.get(SETTINGS_MAIN_KEY)
    if not main and callback.message:
        await state.update_data(
            **{SETTINGS_MAIN_KEY: {"chat_id": callback.message.chat.id, "message_id": callback.message.message_id}},
        )

    async with session_local() as session:
        repo = ClientRepository(session)
        try:
            client = await repo.get_by_telegram_id(telegram_id)
        except ClientNotFound:
            await callback.answer("Команда доступна клиентам после регистрации.", show_alert=True)
            return

    if data == f"{SETTINGS_CB_PREFIX}tz":
        await callback.answer()
        await callback.message.edit_text(
            text="Выбери таймзону:",
            reply_markup=_kb_timezones(),
        )
        return

    if data.startswith(f"{SETTINGS_CB_PREFIX}set_tz:"):
        raw = data.split(":", 2)[2]
        try:
            tz = Timezone(raw)
        except ValueError:
            await callback.answer("Некорректная таймзона.", show_alert=True)
            return

        async with active_session() as session:
            repo = ClientRepository(session)
            await repo.update_by_id(client.id, ClientUpdate(timezone=tz))

        await callback.answer("Таймзона обновлена ✅", show_alert=True)
        await _refresh_settings_message(state=state, bot=callback.bot, telegram_id=telegram_id)
        return

    if data == f"{SETTINGS_CB_PREFIX}toggle_notify":
        current = bool(getattr(client, "notifications_enabled", True))
        new_value = not current
        async with active_session() as session:
            repo = ClientRepository(session)
            await repo.update_by_id(client.id, ClientUpdate(notifications_enabled=new_value))

        await callback.answer("Сохранено ✅", show_alert=True)
        await _refresh_settings_message(state=state, bot=callback.bot, telegram_id=telegram_id)
        return

    if data == f"{SETTINGS_CB_PREFIX}back_menu":
        await callback.answer()
        await _refresh_settings_message(state=state, bot=callback.bot, telegram_id=telegram_id)
        return

    await callback.answer()
