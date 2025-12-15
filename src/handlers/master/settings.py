import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import active_session, session_local
from src.filters.user_role import UserRole
from src.repositories import MasterNotFound, MasterRepository
from src.schemas import MasterUpdate
from src.schemas.enums import Timezone
from src.use_cases.entitlements import EntitlementsService
from src.user_context import ActiveRole
from src.utils import answer_tracked, cleanup_messages, track_message, validate_phone

logger = logging.getLogger(__name__)
router = Router(name=__name__)


SETTINGS_CB_PREFIX = "m:settings:"

SETTINGS_BUCKET = "master_settings"
SETTINGS_MAIN_KEY = "master_settings_main"


class MasterSettingsStates(StatesGroup):
    edit_phone = State()
    edit_work_days = State()
    edit_work_time = State()
    edit_slot_size = State()


def _kb_settings(*, is_pro: bool) -> InlineKeyboardMarkup:
    notify_text = "🔔 Уведомления клиенту (Pro)" if not is_pro else "🔔 Уведомления клиенту"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📞 Телефон", callback_data=f"{SETTINGS_CB_PREFIX}phone")],
            [InlineKeyboardButton(text="🌍 Таймзона", callback_data=f"{SETTINGS_CB_PREFIX}tz")],
            [InlineKeyboardButton(text="📆 Рабочие дни", callback_data=f"{SETTINGS_CB_PREFIX}work_days")],
            [InlineKeyboardButton(text="🕒 Время работы", callback_data=f"{SETTINGS_CB_PREFIX}work_time")],
            [InlineKeyboardButton(text="⏱ Длительность слота", callback_data=f"{SETTINGS_CB_PREFIX}slot_size")],
            [InlineKeyboardButton(text=notify_text, callback_data=f"{SETTINGS_CB_PREFIX}notify")],
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


def _render(*, master_name: str, tz: Timezone, notify_clients: bool, plan_is_pro: bool) -> str:
    plan = "Pro" if plan_is_pro else "Free"
    notify_line = "включены ✅" if notify_clients else "выключены 🚫"
    if not plan_is_pro:
        notify_line += " (доступно в Pro)"
    return (
        "Настройки мастера ⚙️\n\n"
        f"<b>Профиль:</b> {master_name}\n"
        f"<b>Тариф:</b> {plan}\n"
        f"<b>Таймзона:</b> {tz.value}\n"
        f"<b>Уведомления клиенту:</b> {notify_line}\n\n"
        "Что настроим?"
    )


def _render_details(*, master, plan) -> str:
    work_days = ", ".join(str(d + 1) for d in getattr(master, "work_days", [])) or "—"
    work_time = f"{master.start_time:%H:%M}–{master.end_time:%H:%M}" if master.start_time and master.end_time else "—"
    slot_size = f"{master.slot_size_min} мин" if getattr(master, "slot_size_min", None) else "—"
    phone = getattr(master, "phone", None) or "—"
    notify_clients = bool(getattr(master, "notify_clients", True))

    return (
        _render(
            master_name=master.name,
            tz=master.timezone,
            notify_clients=notify_clients,
            plan_is_pro=plan.is_pro,
        )
        + "\n\n"
        f"<b>Телефон:</b> {phone}\n"
        f"<b>Рабочие дни:</b> {work_days}\n"
        f"<b>Время:</b> {work_time}\n"
        f"<b>Слот:</b> {slot_size}"
    )


async def _load_master_and_plan(telegram_id: int):
    async with session_local() as session:
        master_repo = MasterRepository(session)
        entitlements = EntitlementsService(session)
        master = await master_repo.get_by_telegram_id(telegram_id)
        plan = await entitlements.get_plan(master_id=master.id)
        return master, plan


async def open_master_settings(message: Message, state: FSMContext) -> None:
    telegram_id = message.from_user.id
    await cleanup_messages(state, message.bot, bucket=SETTINGS_BUCKET)
    await state.set_state(None)
    try:
        master, plan = await _load_master_and_plan(telegram_id)
    except MasterNotFound:
        await message.answer("Команда доступна мастерам после регистрации.")
        return

    data = await state.get_data()
    main = data.get(SETTINGS_MAIN_KEY) or {}
    prev_chat_id = main.get("chat_id")
    prev_message_id = main.get("message_id")
    if prev_chat_id and prev_message_id:
        try:
            await message.bot.delete_message(chat_id=prev_chat_id, message_id=prev_message_id)
        except Exception:
            logger.debug("master.settings.delete_prev_failed", exc_info=True)

    settings_msg = await message.answer(
        text=_render_details(master=master, plan=plan),
        reply_markup=_kb_settings(is_pro=plan.is_pro),
    )
    await state.update_data(**{
        SETTINGS_MAIN_KEY: {
            "chat_id": settings_msg.chat.id, "message_id": settings_msg.message_id,
        },
    })


async def _refresh_settings_message(*, state: FSMContext, bot, telegram_id: int) -> bool:
    data = await state.get_data()
    main = data.get(SETTINGS_MAIN_KEY) or {}
    chat_id = main.get("chat_id") or telegram_id
    message_id = main.get("message_id")
    if message_id is None:
        return False
    master, plan = await _load_master_and_plan(telegram_id)
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=_render_details(master=master, plan=plan),
        reply_markup=_kb_settings(is_pro=plan.is_pro),
        parse_mode="HTML",
    )
    return True


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith(SETTINGS_CB_PREFIX))
async def settings_callbacks(callback: CallbackQuery, state: FSMContext) -> None:
    telegram_id = callback.from_user.id
    data = callback.data or ""

    if data == f"{SETTINGS_CB_PREFIX}back":
        await callback.answer()
        try:
            await callback.message.delete()
        except Exception:
            logger.debug("master.settings.delete_failed", exc_info=True)
        return

    try:
        master, plan = await _load_master_and_plan(telegram_id)
    except MasterNotFound:
        await callback.answer("Команда доступна мастерам после регистрации.", show_alert=True)
        return

    if data == f"{SETTINGS_CB_PREFIX}cancel_edit":
        await callback.answer("Окей, отменил.", show_alert=True)
        await cleanup_messages(state, callback.bot, bucket=SETTINGS_BUCKET)
        await state.set_state(None)
        await _refresh_settings_message(state=state, bot=callback.bot, telegram_id=telegram_id)
        return

    if data == f"{SETTINGS_CB_PREFIX}back_menu":
        await callback.answer()
        await _refresh_settings_message(state=state, bot=callback.bot, telegram_id=telegram_id)
        return

    if data == f"{SETTINGS_CB_PREFIX}tz":
        await callback.answer()
        await callback.message.edit_text(
            text="Выбери таймзону:",
            reply_markup=_kb_timezones(),
        )
        return

    if data == f"{SETTINGS_CB_PREFIX}phone":
        await callback.answer()
        await state.set_state(MasterSettingsStates.edit_phone)
        await answer_tracked(
            callback.message,
            state,
            text="Введи новый телефон (в формате <code>375291234567</code>):",
            bucket=SETTINGS_BUCKET,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="❌ Отмена", callback_data=f"{SETTINGS_CB_PREFIX}cancel_edit"),
                ]],
            ),
        )
        return

    if data == f"{SETTINGS_CB_PREFIX}work_days":
        await callback.answer()
        await state.set_state(MasterSettingsStates.edit_work_days)
        await answer_tracked(
            callback.message,
            state,
            text="В какие дни недели ты работаешь?\n"
                 "1 — Пн, 2 — Вт, 3 — Ср, 4 — Чт, 5 — Пт, 6 — Сб, 7 — Вс\n\n"
                 "Примеры:\n"
                 "• <code>1-5</code>\n"
                 "• <code>1,3,5</code>",
            bucket=SETTINGS_BUCKET,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="❌ Отмена", callback_data=f"{SETTINGS_CB_PREFIX}cancel_edit"),
                ]],
            ),
        )
        return

    if data == f"{SETTINGS_CB_PREFIX}work_time":
        await callback.answer()
        await state.set_state(MasterSettingsStates.edit_work_time)
        await answer_tracked(
            callback.message,
            state,
            text="Введи рабочее время в формате <code>HH:MM-HH:MM</code>.\n"
                 "Например: <code>10:00-19:00</code>",
            bucket=SETTINGS_BUCKET,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="❌ Отмена", callback_data=f"{SETTINGS_CB_PREFIX}cancel_edit"),
                ]],
            ),
        )
        return

    if data == f"{SETTINGS_CB_PREFIX}slot_size":
        await callback.answer()
        await state.set_state(MasterSettingsStates.edit_slot_size)
        await answer_tracked(
            callback.message,
            state,
            text="Введи длительность слота в минутах.\n"
                 "Например: <code>30</code>, <code>60</code>, <code>90</code>.",
            bucket=SETTINGS_BUCKET,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="❌ Отмена", callback_data=f"{SETTINGS_CB_PREFIX}cancel_edit"),
                ]],
            ),
        )
        return

    if data.startswith(f"{SETTINGS_CB_PREFIX}set_tz:"):
        raw = data.removeprefix(f"{SETTINGS_CB_PREFIX}set_tz:")
        try:
            tz = Timezone(raw)
        except ValueError:
            await callback.answer(text="Некорректная таймзона.", show_alert=True)
            return

        async with active_session() as session:
            master_repo = MasterRepository(session)
            await master_repo.update_by_id(master.id, MasterUpdate(timezone=tz))

        await callback.answer(text="Таймзона обновлена ✅")
        await _refresh_settings_message(state=state, bot=callback.bot, telegram_id=telegram_id)
        return

    if data == f"{SETTINGS_CB_PREFIX}notify":
        if not plan.is_pro:
            await callback.answer("Уведомления клиенту доступны в Pro.", show_alert=True)
            return

        current = bool(getattr(master, "notify_clients", True))
        new_value = not current
        async with active_session() as session:
            master_repo = MasterRepository(session)
            await master_repo.update_by_id(master.id, MasterUpdate(notify_clients=new_value))

        await callback.answer(
            "Уведомления клиенту включены ✅" if new_value else "Уведомления клиенту отключены 🚫",
        )
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


def _parse_time_range(raw: str):
    from datetime import datetime as dt

    text = raw.replace(" ", "")
    if "-" not in text:
        return None
    start_str, end_str = text.split("-", 1)
    try:
        start_dt = dt.strptime(start_str, "%H:%M")
        end_dt = dt.strptime(end_str, "%H:%M")
    except ValueError:
        return None
    start_t = start_dt.time()
    end_t = end_dt.time()
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
    allowed = {15, 20, 30, 45, 60, 90, 120}
    return minutes if minutes in allowed else None


@router.message(UserRole(ActiveRole.MASTER), StateFilter(MasterSettingsStates.edit_phone))
async def save_phone(message: Message, state: FSMContext) -> None:
    await track_message(state, message, bucket=SETTINGS_BUCKET)
    telegram_id = message.from_user.id
    phone = validate_phone((message.text or "").strip())
    if phone is None:
        await message.answer("Не смог разобрать номер. Введи в формате <code>375291234567</code>.")
        return
    async with active_session() as session:
        master_repo = MasterRepository(session)
        master = await master_repo.get_by_telegram_id(telegram_id)
        await master_repo.update_by_id(master.id, MasterUpdate(phone=phone))
    await cleanup_messages(state, message.bot, bucket=SETTINGS_BUCKET)
    await state.set_state(None)
    updated = await _refresh_settings_message(state=state, bot=message.bot, telegram_id=telegram_id)
    if not updated:
        await open_master_settings(message, state)


@router.message(UserRole(ActiveRole.MASTER), StateFilter(MasterSettingsStates.edit_work_days))
async def save_work_days(message: Message, state: FSMContext) -> None:
    await track_message(state, message, bucket=SETTINGS_BUCKET)
    telegram_id = message.from_user.id
    days = _parse_work_days(message.text or "")
    if days is None:
        await message.answer("Не смог разобрать дни. Пример: <code>1-5</code> или <code>1,3,5</code>.")
        return
    async with active_session() as session:
        master_repo = MasterRepository(session)
        master = await master_repo.get_by_telegram_id(telegram_id)
        await master_repo.update_by_id(master.id, MasterUpdate(work_days=days))
    await cleanup_messages(state, message.bot, bucket=SETTINGS_BUCKET)
    await state.set_state(None)
    updated = await _refresh_settings_message(state=state, bot=message.bot, telegram_id=telegram_id)
    if not updated:
        await open_master_settings(message, state)


@router.message(UserRole(ActiveRole.MASTER), StateFilter(MasterSettingsStates.edit_work_time))
async def save_work_time(message: Message, state: FSMContext) -> None:
    await track_message(state, message, bucket=SETTINGS_BUCKET)
    telegram_id = message.from_user.id
    parsed = _parse_time_range(message.text or "")
    if parsed is None:
        await message.answer("Не получилось разобрать время. Пример: <code>10:00-19:00</code>.")
        return
    start_time, end_time = parsed
    async with active_session() as session:
        master_repo = MasterRepository(session)
        master = await master_repo.get_by_telegram_id(telegram_id)
        await master_repo.update_by_id(master.id, MasterUpdate(start_time=start_time, end_time=end_time))
    await cleanup_messages(state, message.bot, bucket=SETTINGS_BUCKET)
    await state.set_state(None)
    updated = await _refresh_settings_message(state=state, bot=message.bot, telegram_id=telegram_id)
    if not updated:
        await open_master_settings(message, state)


@router.message(UserRole(ActiveRole.MASTER), StateFilter(MasterSettingsStates.edit_slot_size))
async def save_slot_size(message: Message, state: FSMContext) -> None:
    await track_message(state, message, bucket=SETTINGS_BUCKET)
    telegram_id = message.from_user.id
    slot_size = _parse_slot_size(message.text or "")
    if slot_size is None:
        await message.answer("Нужны минуты из списка: 15, 20, 30, 45, 60, 90, 120.")
        return
    async with active_session() as session:
        master_repo = MasterRepository(session)
        master = await master_repo.get_by_telegram_id(telegram_id)
        await master_repo.update_by_id(master.id, MasterUpdate(slot_size_min=slot_size))
    await cleanup_messages(state, message.bot, bucket=SETTINGS_BUCKET)
    await state.set_state(None)
    updated = await _refresh_settings_message(state=state, bot=message.bot, telegram_id=telegram_id)
    if not updated:
        await open_master_settings(message, state)
