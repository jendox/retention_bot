from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import active_session, session_local
from src.filters.user_role import UserRole
from src.handlers.shared.guards import rate_limit_callback, rate_limit_message
from src.handlers.shared.ui import safe_bot_delete_message, safe_bot_edit_message_text, safe_delete, safe_edit_text
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.paywall import build_upgrade_button_with_fallback
from src.rate_limiter import RateLimiter
from src.repositories import MasterNotFound, MasterRepository
from src.schemas import MasterUpdate
from src.schemas.enums import Timezone
from src.settings import get_settings
from src.texts import billing as billing_txt, common as common_txt, master_settings as txt
from src.texts.buttons import btn_back, btn_cancel, btn_close
from src.use_cases.entitlements import EntitlementsService
from src.user_context import ActiveRole
from src.utils import answer_tracked, cleanup_messages, track_message, validate_phone

router = Router(name=__name__)
ev = EventLogger(__name__)


SETTINGS_CB_PREFIX = "m:settings:"

SETTINGS_BUCKET = "master_settings"
SETTINGS_MAIN_KEY = "master_settings_main"


class MasterSettingsStates(StatesGroup):
    edit_phone = State()
    edit_work_days = State()
    edit_work_time = State()
    edit_slot_size = State()


def _kb_settings(*, notify_clients: bool, plan_is_pro: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=txt.btn_phone(), callback_data=f"{SETTINGS_CB_PREFIX}phone")],
            [InlineKeyboardButton(text=txt.btn_timezone(), callback_data=f"{SETTINGS_CB_PREFIX}tz")],
            [InlineKeyboardButton(text=txt.btn_work_days(), callback_data=f"{SETTINGS_CB_PREFIX}work_days")],
            [InlineKeyboardButton(text=txt.btn_work_time(), callback_data=f"{SETTINGS_CB_PREFIX}work_time")],
            [InlineKeyboardButton(text=txt.btn_slot_size(), callback_data=f"{SETTINGS_CB_PREFIX}slot_size")],
            [
                InlineKeyboardButton(
                    text=txt.btn_notify(notify_clients=notify_clients, plan_is_pro=plan_is_pro),
                    callback_data=f"{SETTINGS_CB_PREFIX}notify",
                ),
            ],
            [InlineKeyboardButton(text=txt.btn_tariffs(), callback_data=f"{SETTINGS_CB_PREFIX}tariffs")],
            [InlineKeyboardButton(text=btn_close(), callback_data=f"{SETTINGS_CB_PREFIX}back")],
        ],
    )


def _kb_tariffs(
    *,
    contact: str,
    primary_callback_data: str,
    primary_text: str | None,
    secondary_text: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if primary_text:
        rows.append(
            [
                build_upgrade_button_with_fallback(
                    contact=contact,
                    text=primary_text,
                    callback_data=primary_callback_data,
                    force_callback=True,
                ),
            ],
        )
    rows.append([InlineKeyboardButton(text=secondary_text, callback_data=f"{SETTINGS_CB_PREFIX}back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
    rows.append([InlineKeyboardButton(text=btn_close(), callback_data="m:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _render(*, master_name: str, tz: Timezone, notify_clients: bool, plan_is_pro: bool) -> str:
    return txt.render_main(
        master_name=master_name,
        tz_value=str(tz.value),
        notify_clients=notify_clients,
        plan_is_pro=plan_is_pro,
    )


def _render_details(*, master, plan) -> str:
    work_days = ", ".join(str(d + 1) for d in getattr(master, "work_days", [])) or common_txt.placeholder_empty()
    work_time = (
        f"{master.start_time:%H:%M}–{master.end_time:%H:%M}"
        if master.start_time and master.end_time
        else common_txt.placeholder_empty()
    )
    slot_size = (
        txt.minutes(value=int(master.slot_size_min))
        if getattr(master, "slot_size_min", None)
        else common_txt.placeholder_empty()
    )
    phone = getattr(master, "phone", None) or common_txt.placeholder_empty()
    notify_clients = bool(getattr(master, "notify_clients", True))

    return _render(
        master_name=master.name,
        tz=master.timezone,
        notify_clients=notify_clients,
        plan_is_pro=plan.is_pro,
    ) + txt.render_details(
        phone=str(phone),
        work_days=str(work_days),
        work_time=str(work_time),
        slot_size=str(slot_size),
    )


async def _load_master_and_plan(telegram_id: int):
    async with session_local() as session:
        master_repo = MasterRepository(session)
        entitlements = EntitlementsService(session)
        master = await master_repo.get_by_telegram_id(telegram_id)
        plan = await entitlements.get_plan(master_id=master.id)
        return master, plan


async def open_master_settings(
    message: Message,
    state: FSMContext,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="master_settings", step="open")
    if not await rate_limit_message(message, rate_limiter, name="master_settings:open", ttl_sec=2):
        return
    ev.info("master_settings.open")
    telegram_id = message.from_user.id
    await cleanup_messages(state, message.bot, bucket=SETTINGS_BUCKET)
    await state.set_state(None)
    try:
        master, plan = await _load_master_and_plan(telegram_id)
    except MasterNotFound:
        ev.warning("master_settings.master_not_found")
        await message.answer(txt.master_only(), parse_mode="HTML")
        return

    data = await state.get_data()
    main = data.get(SETTINGS_MAIN_KEY) or {}
    prev_chat_id = main.get("chat_id")
    prev_message_id = main.get("message_id")
    if prev_chat_id and prev_message_id:
        await safe_bot_delete_message(
            message.bot,
            chat_id=int(prev_chat_id),
            message_id=int(prev_message_id),
            ev=ev,
            event="master.settings.delete_prev_failed",
        )

    settings_msg = await message.answer(
        text=_render_details(master=master, plan=plan),
        reply_markup=_kb_settings(
            notify_clients=bool(getattr(master, "notify_clients", True)),
            plan_is_pro=plan.is_pro,
        ),
        parse_mode="HTML",
    )
    await state.update_data(
        **{
            SETTINGS_MAIN_KEY: {
                "chat_id": settings_msg.chat.id,
                "message_id": settings_msg.message_id,
            },
        },
    )


async def _refresh_settings_message(*, state: FSMContext, bot, telegram_id: int) -> bool:
    data = await state.get_data()
    main = data.get(SETTINGS_MAIN_KEY) or {}
    chat_id = main.get("chat_id") or telegram_id
    message_id = main.get("message_id")
    if message_id is None:
        return False
    master, plan = await _load_master_and_plan(telegram_id)
    await safe_bot_edit_message_text(
        bot,
        chat_id=int(chat_id),
        message_id=int(message_id),
        text=_render_details(master=master, plan=plan),
        reply_markup=_kb_settings(
            notify_clients=bool(getattr(master, "notify_clients", True)),
            plan_is_pro=plan.is_pro,
        ),
        parse_mode="HTML",
        event="master.settings.refresh_failed",
    )
    return True


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith(SETTINGS_CB_PREFIX))
async def settings_callbacks(  # noqa: C901, PLR0911, PLR0912, PLR0914, PLR0915
    callback: CallbackQuery,
    state: FSMContext,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="master_settings", step="callback")
    if not await rate_limit_callback(callback, rate_limiter, name="master_settings:callback", ttl_sec=1):
        return
    telegram_id = callback.from_user.id
    data = callback.data or ""
    action = data.removeprefix(SETTINGS_CB_PREFIX) if data.startswith(SETTINGS_CB_PREFIX) else "unknown"
    action_key = action.split(":", 1)[0] if action else "unknown"
    if action_key == "set_tz":
        action_key = "set_tz"
    ev.info("master_settings.action", action=action_key)

    if data == f"{SETTINGS_CB_PREFIX}back":
        await callback.answer()
        if callback.message is not None:
            await safe_delete(callback.message, event="master.settings.delete_failed")
        return

    try:
        master, plan = await _load_master_and_plan(telegram_id)
    except MasterNotFound:
        ev.warning("master_settings.master_not_found")
        await callback.answer(txt.master_only(), show_alert=True)
        return

    if data == f"{SETTINGS_CB_PREFIX}cancel_edit":
        await callback.answer(txt.cancelled(), show_alert=True)
        await cleanup_messages(state, callback.bot, bucket=SETTINGS_BUCKET)
        await state.set_state(None)
        await _refresh_settings_message(state=state, bot=callback.bot, telegram_id=telegram_id)
        return

    if data == f"{SETTINGS_CB_PREFIX}back_menu":
        await callback.answer()
        await _refresh_settings_message(state=state, bot=callback.bot, telegram_id=telegram_id)
        return

    if data == f"{SETTINGS_CB_PREFIX}tariffs":
        await callback.answer()
        settings = get_settings()
        contact = settings.billing.contact
        price = settings.billing.pro_price_byn
        days = settings.billing.pro_days

        source = str(plan.source)
        plan_label = "Pro" if plan.is_pro else "Free"
        if source == "trial":
            plan_label = "Pro (trial)"
        elif source == "paid":
            plan_label = "Pro (paid)"

        msg = billing_txt.tariffs_message(
            plan_label=plan_label,
            source=source,
            active_until=plan.active_until,
            pro_days=int(days) if days is not None else None,
            pro_price_byn=float(price) if price is not None else None,
        )
        if callback.message is not None:
            primary_text: str | None = None
            secondary_text = billing_txt.tariffs_secondary_button(source=source)
            primary_cb = "billing:pro:start" if source == "free" else "billing:pro:renew"
            if price is not None and days is not None:
                primary_text = billing_txt.tariffs_primary_button(
                    source=source,
                    pro_days=int(days),
                    pro_price_byn=float(price),
                )
            await safe_edit_text(
                callback.message,
                text=msg,
                reply_markup=_kb_tariffs(
                    contact=contact,
                    primary_callback_data=primary_cb,
                    primary_text=primary_text,
                    secondary_text=secondary_text,
                ),
                parse_mode="HTML",
                ev=ev,
                event="master.settings.tariffs_edit_failed",
            )
        return

    if data == f"{SETTINGS_CB_PREFIX}tz":
        await callback.answer()
        if callback.message is not None:
            await safe_edit_text(
                callback.message,
                text=txt.choose_timezone(),
                reply_markup=_kb_timezones(),
                event="master.settings.edit_failed",
            )
        return

    if data == f"{SETTINGS_CB_PREFIX}phone":
        await callback.answer()
        await state.set_state(MasterSettingsStates.edit_phone)
        await answer_tracked(
            callback.message,
            state,
            text=txt.ask_new_phone(),
            bucket=SETTINGS_BUCKET,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=btn_cancel(), callback_data=f"{SETTINGS_CB_PREFIX}cancel_edit")],
                    [InlineKeyboardButton(text=btn_close(), callback_data="m:close")],
                ],
            ),
        )
        return

    if data == f"{SETTINGS_CB_PREFIX}work_days":
        await callback.answer()
        await state.set_state(MasterSettingsStates.edit_work_days)
        await answer_tracked(
            callback.message,
            state,
            text=txt.ask_work_days(),
            bucket=SETTINGS_BUCKET,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=btn_cancel(), callback_data=f"{SETTINGS_CB_PREFIX}cancel_edit")],
                    [InlineKeyboardButton(text=btn_close(), callback_data="m:close")],
                ],
            ),
        )
        return

    if data == f"{SETTINGS_CB_PREFIX}work_time":
        await callback.answer()
        await state.set_state(MasterSettingsStates.edit_work_time)
        await answer_tracked(
            callback.message,
            state,
            text=txt.ask_work_time(),
            bucket=SETTINGS_BUCKET,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=btn_cancel(), callback_data=f"{SETTINGS_CB_PREFIX}cancel_edit")],
                    [InlineKeyboardButton(text=btn_close(), callback_data="m:close")],
                ],
            ),
        )
        return

    if data == f"{SETTINGS_CB_PREFIX}slot_size":
        await callback.answer()
        await state.set_state(MasterSettingsStates.edit_slot_size)
        await answer_tracked(
            callback.message,
            state,
            text=txt.ask_slot_size(),
            bucket=SETTINGS_BUCKET,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=btn_cancel(), callback_data=f"{SETTINGS_CB_PREFIX}cancel_edit")],
                    [InlineKeyboardButton(text=btn_close(), callback_data="m:close")],
                ],
            ),
        )
        return

    if data.startswith(f"{SETTINGS_CB_PREFIX}set_tz:"):
        raw = data.removeprefix(f"{SETTINGS_CB_PREFIX}set_tz:")
        try:
            tz = Timezone(raw)
        except ValueError:
            await callback.answer(text=txt.invalid_timezone(), show_alert=True)
            return

        async with active_session() as session:
            master_repo = MasterRepository(session)
            await master_repo.update_by_id(master.id, MasterUpdate(timezone=tz))

        await callback.answer(text=txt.timezone_updated())
        await _refresh_settings_message(state=state, bot=callback.bot, telegram_id=telegram_id)
        return

    if data == f"{SETTINGS_CB_PREFIX}notify":
        if not plan.is_pro:
            await callback.answer(txt.notify_pro_only(), show_alert=True)
            return

        current = bool(getattr(master, "notify_clients", True))
        new_value = not current
        async with active_session() as session:
            master_repo = MasterRepository(session)
            await master_repo.update_by_id(master.id, MasterUpdate(notify_clients=new_value))

        await callback.answer(txt.notify_toggled(enabled=new_value))
        await _refresh_settings_message(state=state, bot=callback.bot, telegram_id=telegram_id)
        return

    # Fallback: re-render
    await callback.answer()
    await _refresh_settings_message(state=state, bot=callback.bot, telegram_id=telegram_id)


def _parse_work_days(raw: str) -> list[int] | None:
    text = raw.replace(" ", "")
    if not text:
        return None
    monday = 1
    sunday = 7
    try:
        if "-" in text and "," not in text:
            start_str, end_str = text.split("-", 1)
            start = int(start_str)
            end = int(end_str)
            if not (monday <= start <= sunday and monday <= end <= sunday and start <= end):
                return None
            days = [day - 1 for day in range(start, end + 1)]
        else:
            days = []
            for part in text.split(","):
                day = int(part)
                if not (monday <= day <= sunday):
                    return None
                days.append(day - 1)
        return sorted(set(days)) or None
    except ValueError:
        return None


def _parse_time_range(raw: str):  # noqa: C901
    from datetime import time as t

    dash_translation = str.maketrans(dict.fromkeys("‐‑‒–—−", "-"))
    hours_max = 23
    minutes_max = 59

    def parse_time_value(value: str) -> t | None:
        value = value.strip()
        if ":" in value:
            hours_str, minutes_str = value.split(":", 1)
            try:
                hours = int(hours_str)
                minutes = int(minutes_str)
            except ValueError:
                return None
            if not (0 <= hours <= hours_max and 0 <= minutes <= minutes_max):
                return None
            return t(hour=hours, minute=minutes)

        try:
            hours = int(value)
        except ValueError:
            return None
        if not (0 <= hours <= hours_max):
            return None
        return t(hour=hours, minute=0)

    text = raw.replace(" ", "").translate(dash_translation)
    if "-" not in text:
        return None
    start_str, end_str = text.split("-", 1)
    start_t = parse_time_value(start_str)
    end_t = parse_time_value(end_str)
    if start_t is None or end_t is None:
        return None
    if start_t >= end_t:
        return None
    return start_t, end_t


def _parse_slot_size(raw: str) -> int | None:
    text = raw.strip()
    if not text:
        return None
    try:
        minutes = int(text)
    except ValueError:
        return None
    min_minutes = 5
    max_minutes = 240
    step = 5
    if minutes < min_minutes or minutes > max_minutes:
        return None
    return minutes if minutes % step == 0 else None


@router.message(UserRole(ActiveRole.MASTER), StateFilter(MasterSettingsStates.edit_phone))
async def save_phone(message: Message, state: FSMContext) -> None:
    bind_log_context(flow="master_settings", step="edit_phone_save")
    await track_message(state, message, bucket=SETTINGS_BUCKET)
    telegram_id = message.from_user.id
    phone = validate_phone((message.text or "").strip())
    if phone is None:
        ev.debug("master_settings.input_invalid", field="phone", reason="invalid")
        await message.answer(txt.invalid_phone(), parse_mode="HTML")
        return
    async with active_session() as session:
        master_repo = MasterRepository(session)
        master = await master_repo.get_by_telegram_id(telegram_id)
        await master_repo.update_by_id(master.id, MasterUpdate(phone=phone))
    ev.info("master_settings.phone_updated")
    await cleanup_messages(state, message.bot, bucket=SETTINGS_BUCKET)
    await state.set_state(None)
    updated = await _refresh_settings_message(state=state, bot=message.bot, telegram_id=telegram_id)
    if not updated:
        await open_master_settings(message, state)


@router.message(UserRole(ActiveRole.MASTER), StateFilter(MasterSettingsStates.edit_work_days))
async def save_work_days(message: Message, state: FSMContext) -> None:
    bind_log_context(flow="master_settings", step="edit_work_days_save")
    await track_message(state, message, bucket=SETTINGS_BUCKET)
    telegram_id = message.from_user.id
    days = _parse_work_days(message.text or "")
    if days is None:
        ev.debug("master_settings.input_invalid", field="work_days", reason="invalid")
        await message.answer(txt.invalid_days(), parse_mode="HTML")
        return
    async with active_session() as session:
        master_repo = MasterRepository(session)
        master = await master_repo.get_by_telegram_id(telegram_id)
        await master_repo.update_by_id(master.id, MasterUpdate(work_days=days))
    ev.info("master_settings.work_days_updated", days_count=len(days))
    await cleanup_messages(state, message.bot, bucket=SETTINGS_BUCKET)
    await state.set_state(None)
    updated = await _refresh_settings_message(state=state, bot=message.bot, telegram_id=telegram_id)
    if not updated:
        await open_master_settings(message, state)


@router.message(UserRole(ActiveRole.MASTER), StateFilter(MasterSettingsStates.edit_work_time))
async def save_work_time(message: Message, state: FSMContext) -> None:
    bind_log_context(flow="master_settings", step="edit_work_time_save")
    await track_message(state, message, bucket=SETTINGS_BUCKET)
    telegram_id = message.from_user.id
    parsed = _parse_time_range(message.text or "")
    if parsed is None:
        ev.debug("master_settings.input_invalid", field="work_time", reason="invalid")
        await message.answer(txt.invalid_work_time(), parse_mode="HTML")
        return
    start_time, end_time = parsed
    async with active_session() as session:
        master_repo = MasterRepository(session)
        master = await master_repo.get_by_telegram_id(telegram_id)
        await master_repo.update_by_id(master.id, MasterUpdate(start_time=start_time, end_time=end_time))
    ev.info("master_settings.work_time_updated")
    await cleanup_messages(state, message.bot, bucket=SETTINGS_BUCKET)
    await state.set_state(None)
    updated = await _refresh_settings_message(state=state, bot=message.bot, telegram_id=telegram_id)
    if not updated:
        await open_master_settings(message, state)


@router.message(UserRole(ActiveRole.MASTER), StateFilter(MasterSettingsStates.edit_slot_size))
async def save_slot_size(message: Message, state: FSMContext) -> None:
    bind_log_context(flow="master_settings", step="edit_slot_size_save")
    await track_message(state, message, bucket=SETTINGS_BUCKET)
    telegram_id = message.from_user.id
    slot_size = _parse_slot_size(message.text or "")
    if slot_size is None:
        ev.debug("master_settings.input_invalid", field="slot_size", reason="invalid")
        await message.answer(txt.invalid_slot_size(), parse_mode="HTML")
        return
    async with active_session() as session:
        master_repo = MasterRepository(session)
        master = await master_repo.get_by_telegram_id(telegram_id)
        await master_repo.update_by_id(master.id, MasterUpdate(slot_size_min=slot_size))
    ev.info("master_settings.slot_size_updated", slot_size_min=int(slot_size))
    await cleanup_messages(state, message.bot, bucket=SETTINGS_BUCKET)
    await state.set_state(None)
    updated = await _refresh_settings_message(state=state, bot=message.bot, telegram_id=telegram_id)
    if not updated:
        await open_master_settings(message, state)
