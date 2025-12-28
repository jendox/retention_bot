from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import active_session, session_local
from src.filters.user_role import UserRole
from src.handlers.shared.flow import context_lost
from src.handlers.shared.guards import rate_limit_callback, rate_limit_message
from src.handlers.shared.ui import safe_bot_delete_message, safe_bot_edit_message_text, safe_delete, safe_edit_text
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.privacy import ConsentRole
from src.rate_limiter import RateLimiter
from src.repositories import ClientNotFound, ClientRepository, MasterNotFound, MasterRepository
from src.repositories.consent import ConsentRepository
from src.schemas import ClientUpdate
from src.schemas.enums import Timezone
from src.texts import client_settings as txt, common as common_txt, personal_data as pd_txt
from src.texts.buttons import btn_back, btn_close
from src.user_context import ActiveRole, UserContextStorage
from src.utils import cleanup_messages, format_phone_display, track_message, validate_phone

router = Router(name=__name__)
ev = EventLogger(__name__)

SETTINGS_CB_PREFIX = "c:settings:"
SETTINGS_MAIN_KEY = "client_settings_main"
SETTINGS_BUCKET = "client_settings"


class ClientSettingsStates(StatesGroup):
    edit_phone = State()


def _kb_settings(*, notifications_enabled: bool) -> InlineKeyboardMarkup:
    notify_text = txt.btn_notifications(enabled=notifications_enabled)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=txt.btn_timezone(), callback_data=f"{SETTINGS_CB_PREFIX}tz")],
            [InlineKeyboardButton(text=txt.btn_phone(), callback_data=f"{SETTINGS_CB_PREFIX}edit_phone")],
            [InlineKeyboardButton(text=notify_text, callback_data=f"{SETTINGS_CB_PREFIX}toggle_notify")],
            [InlineKeyboardButton(text=txt.btn_delete_data(), callback_data=f"{SETTINGS_CB_PREFIX}delete_data")],
            [InlineKeyboardButton(text=btn_close(), callback_data=f"{SETTINGS_CB_PREFIX}back")],
        ],
    )


def _kb_delete_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Удалить", callback_data=f"{SETTINGS_CB_PREFIX}delete_confirm")],
            [InlineKeyboardButton(text=btn_back(), callback_data=f"{SETTINGS_CB_PREFIX}back_menu")],
            [InlineKeyboardButton(text=btn_close(), callback_data=f"{SETTINGS_CB_PREFIX}back")],
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
    rows.append([InlineKeyboardButton(text=btn_back(), callback_data=f"{SETTINGS_CB_PREFIX}back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _render(*, name: str, phone: str, tz: Timezone, notifications_enabled: bool) -> str:
    return txt.render_settings(
        name=name,
        phone=phone,
        tz_value=str(tz.value),
        notifications_enabled=notifications_enabled,
    )


def _get_main_ref(data: dict, *, telegram_id: int) -> tuple[int, int] | None:
    main = data.get(SETTINGS_MAIN_KEY) or {}
    chat_id = main.get("chat_id") or telegram_id
    message_id = main.get("message_id")
    if message_id is None:
        return None
    return int(chat_id), int(message_id)


async def _set_main_ref(state: FSMContext, *, chat_id: int, message_id: int) -> None:
    await state.update_data(**{SETTINGS_MAIN_KEY: {"chat_id": int(chat_id), "message_id": int(message_id)}})


async def _clear_main_ref(state: FSMContext) -> None:
    await state.update_data(**{SETTINGS_MAIN_KEY: {}})


async def _load_client(telegram_id: int):
    async with session_local() as session:
        repo = ClientRepository(session)
        return await repo.get_by_telegram_id(telegram_id)


async def _render_and_edit_main(
    *,
    state: FSMContext,
    bot,
    telegram_id: int,
) -> bool:
    data = await state.get_data()
    ref = _get_main_ref(data, telegram_id=telegram_id)
    if ref is None:
        return False
    chat_id, message_id = ref

    try:
        client = await _load_client(telegram_id)
    except ClientNotFound:
        return False

    return await safe_bot_edit_message_text(
        bot,
        chat_id=chat_id,
        message_id=message_id,
        text=_render(
            name=client.name,
            phone=format_phone_display(str(getattr(client, "phone", ""))),
            tz=client.timezone,
            notifications_enabled=bool(getattr(client, "notifications_enabled", True)),
        ),
        reply_markup=_kb_settings(notifications_enabled=bool(getattr(client, "notifications_enabled", True))),
        parse_mode="HTML",
        ev=ev,
        event="client_settings.edit_main_failed",
    )


async def open_client_settings(
    message: Message,
    state: FSMContext,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="client_settings", step="open")
    telegram_id = message.from_user.id
    if not await rate_limit_message(message, rate_limiter, name="client_settings:open", ttl_sec=2):
        return

    try:
        client = await _load_client(telegram_id)
    except ClientNotFound:
        await message.answer(txt.client_only())
        return

    data = await state.get_data()
    ref = _get_main_ref(data, telegram_id=telegram_id)
    if ref is not None:
        chat_id, message_id = ref
        await safe_bot_delete_message(
            message.bot,
            chat_id=chat_id,
            message_id=message_id,
            ev=ev,
            event="client_settings.delete_prev_failed",
        )

    settings_msg = await message.answer(
        text=_render(
            name=client.name,
            phone=format_phone_display(str(getattr(client, "phone", ""))),
            tz=client.timezone,
            notifications_enabled=bool(getattr(client, "notifications_enabled", True)),
        ),
        reply_markup=_kb_settings(notifications_enabled=bool(getattr(client, "notifications_enabled", True))),
        parse_mode="HTML",
    )
    await _set_main_ref(state, chat_id=settings_msg.chat.id, message_id=settings_msg.message_id)
    await cleanup_messages(state, message.bot, bucket=SETTINGS_BUCKET)
    await state.set_state(None)


async def _show_timezone_menu(callback: CallbackQuery) -> bool:
    if callback.message is None:
        return False
    return await safe_edit_text(
        callback.message,
        text=txt.choose_timezone(),
        reply_markup=_kb_timezones(),
        parse_mode="HTML",
        ev=ev,
        event="client_settings.edit_timezone_menu_failed",
    )


async def _edit_main_or_context_lost(
    callback: CallbackQuery,
    *,
    state: FSMContext,
    telegram_id: int,
    reason: str,
) -> bool:
    ok = await _render_and_edit_main(state=state, bot=callback.bot, telegram_id=telegram_id)
    if ok:
        return True
    await context_lost(callback, state, bucket=SETTINGS_MAIN_KEY, reason=reason)
    return False


async def _handle_back(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is not None:
        await safe_delete(callback.message, ev=ev, event="client_settings.delete_failed")
    await cleanup_messages(state, callback.bot, bucket=SETTINGS_BUCKET)
    await _clear_main_ref(state)
    await state.set_state(None)


async def _ensure_main_ref_from_message(callback: CallbackQuery, state: FSMContext, *, telegram_id: int) -> None:
    data = await state.get_data()
    if _get_main_ref(data, telegram_id=telegram_id) is not None:
        return
    if callback.message is None:
        return
    await _set_main_ref(state, chat_id=callback.message.chat.id, message_id=callback.message.message_id)


async def _handle_choose_timezone(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    ok = await _show_timezone_menu(callback)
    if not ok:
        await context_lost(callback, state, bucket=SETTINGS_MAIN_KEY, reason="missing_message_on_tz_menu")


async def _handle_set_timezone(
    callback: CallbackQuery,
    *,
    state: FSMContext,
    telegram_id: int,
    client_id: int,
    raw_tz: str,
) -> None:
    try:
        tz = Timezone(raw_tz)
    except ValueError:
        ev.debug("client_settings.input_invalid", field="timezone", reason="invalid")
        await callback.answer(txt.invalid_timezone(), show_alert=True)
        return

    async with active_session() as session:
        repo = ClientRepository(session)
        await repo.update_by_id(client_id, ClientUpdate(timezone=tz))

    ev.info("client_settings.timezone_updated", tz=str(tz.value))
    await callback.answer(txt.timezone_updated())
    await _edit_main_or_context_lost(
        callback,
        state=state,
        telegram_id=telegram_id,
        reason="missing_main_ref_after_set_tz",
    )


async def _handle_toggle_notify(
    callback: CallbackQuery,
    *,
    state: FSMContext,
    telegram_id: int,
    client_id: int,
    current_enabled: bool,
) -> None:
    new_value = not bool(current_enabled)
    async with active_session() as session:
        repo = ClientRepository(session)
        await repo.update_by_id(client_id, ClientUpdate(notifications_enabled=new_value))

    ev.info("client_settings.notifications_toggled", enabled=bool(new_value))
    await callback.answer(txt.saved())
    await _edit_main_or_context_lost(
        callback,
        state=state,
        telegram_id=telegram_id,
        reason="missing_main_ref_after_toggle_notify",
    )


async def _handle_back_menu(callback: CallbackQuery, *, state: FSMContext, telegram_id: int) -> None:
    await callback.answer()
    await _edit_main_or_context_lost(
        callback,
        state=state,
        telegram_id=telegram_id,
        reason="missing_main_ref_on_back_menu",
    )
    await cleanup_messages(state, callback.bot, bucket=SETTINGS_BUCKET)
    await state.set_state(None)


def _kb_phone_prompt() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=btn_back(), callback_data=f"{SETTINGS_CB_PREFIX}back_menu")]],
    )


async def _handle_edit_phone(callback: CallbackQuery, *, state: FSMContext) -> None:
    await callback.answer()
    await cleanup_messages(state, callback.bot, bucket=SETTINGS_BUCKET)
    ev.info("client_settings.edit_phone_start")
    ok = await safe_edit_text(
        callback.message,
        text=txt.ask_new_phone(),
        reply_markup=_kb_phone_prompt(),
        parse_mode="HTML",
        ev=ev,
        event="client_settings.edit_phone_prompt_failed",
    )
    if not ok:
        await context_lost(callback, state, bucket=SETTINGS_MAIN_KEY, reason="missing_message_on_edit_phone")
        return
    await state.set_state(ClientSettingsStates.edit_phone)


async def _load_client_or_alert(callback: CallbackQuery, state: FSMContext, *, telegram_id: int):
    try:
        return await _load_client(telegram_id)
    except ClientNotFound:
        ev.warning("client_settings.client_not_found")
        await callback.answer(txt.client_only_alert(), show_alert=True)
        await _clear_main_ref(state)
        return None


def _parse_action(data: str) -> tuple[str, str | None]:
    if not data.startswith(SETTINGS_CB_PREFIX):
        return "unknown", None

    suffix = data.removeprefix(SETTINGS_CB_PREFIX)
    mapping = {
        "back": "back",
        "tz": "tz",
        "edit_phone": "edit_phone",
        "toggle_notify": "toggle_notify",
        "back_menu": "back_menu",
        "delete_data": "delete_data",
        "delete_confirm": "delete_confirm",
    }
    if suffix in mapping:
        return mapping[suffix], None

    if suffix.startswith("set_tz:"):
        return "set_tz", suffix.split(":", 1)[1]

    return "unknown", None


@router.callback_query(UserRole(ActiveRole.CLIENT), F.data.startswith(SETTINGS_CB_PREFIX))
async def settings_callbacks(
    callback: CallbackQuery,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="client_settings", step="callback")
    if not await rate_limit_callback(callback, rate_limiter, name="client_settings:callback", ttl_sec=1):
        return

    await _settings_callbacks_impl(
        callback=callback,
        state=state,
        user_ctx_storage=user_ctx_storage,
    )


async def _settings_callbacks_impl(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
) -> None:
    telegram_id = callback.from_user.id
    data = callback.data or ""
    action, arg = _parse_action(data)
    ev.info("client_settings.action", action=str(action))

    if action == "back":
        await _handle_back(callback, state)
        return

    await _ensure_main_ref_from_message(callback, state, telegram_id=telegram_id)

    if action in {"set_tz", "toggle_notify"}:
        client = await _load_client_or_alert(callback, state, telegram_id=telegram_id)
        if client is None:
            return
        await _dispatch_with_client(
            callback,
            state=state,
            telegram_id=telegram_id,
            action=action,
            arg=arg,
            client=client,
        )
        return

    await _dispatch_without_client(
        callback,
        state=state,
        telegram_id=telegram_id,
        action=action,
        user_ctx_storage=user_ctx_storage,
    )


async def _dispatch_with_client(
    callback: CallbackQuery,
    *,
    state: FSMContext,
    telegram_id: int,
    action: str,
    arg: str | None,
    client,
) -> None:
    if action == "set_tz":
        await _handle_set_timezone(
            callback,
            state=state,
            telegram_id=telegram_id,
            client_id=client.id,
            raw_tz=str(arg or ""),
        )
        return
    if action == "toggle_notify":
        await _handle_toggle_notify(
            callback,
            state=state,
            telegram_id=telegram_id,
            client_id=client.id,
            current_enabled=bool(getattr(client, "notifications_enabled", True)),
        )
        return
    await callback.answer()


async def _dispatch_without_client(  # noqa: C901, PLR0912
    callback: CallbackQuery,
    *,
    state: FSMContext,
    telegram_id: int,
    action: str,
    user_ctx_storage: UserContextStorage,
) -> None:
    if action == "tz":
        await _handle_choose_timezone(callback, state)
        return
    if action == "edit_phone":
        await _handle_edit_phone(callback, state=state)
        return
    if action == "back_menu":
        await _handle_back_menu(callback, state=state, telegram_id=telegram_id)
        return
    if action == "delete_data":
        await callback.answer()
        ev.info("pd.delete_prompt_shown", role="client")
        if callback.message is None:
            await context_lost(callback, state, bucket=SETTINGS_MAIN_KEY, reason="missing_message_on_delete")
            return
        await safe_edit_text(
            callback.message,
            text=pd_txt.delete_client_warning(),
            reply_markup=_kb_delete_confirm(),
            parse_mode="HTML",
            ev=ev,
            event="client_settings.delete_prompt_failed",
        )
        return
    if action == "delete_confirm":
        await callback.answer()
        ev.info("pd.delete_confirmed", role="client")
        async with active_session() as session:
            deleted = await ClientRepository(session).delete_by_telegram_id(telegram_id)
            await ConsentRepository(session).delete_consent(telegram_id=telegram_id, role=str(ConsentRole.CLIENT.value))
            master_exists = True
            try:
                await MasterRepository(session).get_by_telegram_id(telegram_id)
            except MasterNotFound:
                master_exists = False

        if callback.message is not None:
            await safe_delete(callback.message, ev=ev, event="client_settings.delete_main_failed")
        await cleanup_messages(state, callback.bot, bucket=SETTINGS_BUCKET)
        await _clear_main_ref(state)
        await state.clear()

        if master_exists:
            await user_ctx_storage.set_role(telegram_id, ActiveRole.MASTER)
        else:
            await user_ctx_storage.clear_role(telegram_id)

        if deleted:
            ev.info("pd.deleted", role="client", deleted=True)
            await callback.bot.send_message(chat_id=telegram_id, text=pd_txt.deleted_done(), parse_mode="HTML")
        else:
            ev.info("pd.deleted", role="client", deleted=False)
            await callback.bot.send_message(chat_id=telegram_id, text=common_txt.context_lost(), parse_mode="HTML")
        return
    await callback.answer()


@router.message(UserRole(ActiveRole.CLIENT), StateFilter(ClientSettingsStates.edit_phone))
async def save_phone(message: Message, state: FSMContext, rate_limiter: RateLimiter | None = None) -> None:
    bind_log_context(flow="client_settings", step="edit_phone_save")
    if not await rate_limit_message(message, rate_limiter, name="client_settings:edit_phone", ttl_sec=1):
        return

    await track_message(state, message, bucket=SETTINGS_BUCKET)
    raw = (message.text or "").strip()
    phone = validate_phone(raw)
    if phone is None:
        ev.debug("client_settings.input_invalid", field="phone", reason="invalid")
        await cleanup_messages(state, message.bot, bucket=SETTINGS_BUCKET)
        data = await state.get_data()
        ref = _get_main_ref(data, telegram_id=message.from_user.id)
        if ref is None:
            await message.answer(common_txt.context_lost())
            await state.clear()
            return
        chat_id, message_id = ref
        await safe_bot_edit_message_text(
            message.bot,
            chat_id=chat_id,
            message_id=message_id,
            text=txt.phone_not_recognized(),
            reply_markup=_kb_phone_prompt(),
            parse_mode="HTML",
            ev=ev,
            event="client_settings.edit_phone_invalid_failed",
        )
        return

    telegram_id = message.from_user.id
    try:
        client = await _load_client(telegram_id)
    except ClientNotFound:
        ev.warning("client_settings.client_not_found")
        await cleanup_messages(state, message.bot, bucket=SETTINGS_BUCKET)
        await message.answer(txt.client_only())
        await _clear_main_ref(state)
        await state.set_state(None)
        return

    async with active_session() as session:
        repo = ClientRepository(session)
        await repo.update_by_id(client.id, ClientUpdate(phone=phone))

    ev.info("client_settings.phone_updated")
    await cleanup_messages(state, message.bot, bucket=SETTINGS_BUCKET)
    await _render_and_edit_main(state=state, bot=message.bot, telegram_id=telegram_id)
    await state.set_state(None)
