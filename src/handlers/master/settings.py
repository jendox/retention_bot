from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.core.sa import active_session, session_local
from src.filters.user_role import UserRole
from src.handlers.shared.guards import rate_limit_callback, rate_limit_message
from src.handlers.shared.ui import safe_bot_delete_message, safe_bot_edit_message_text, safe_delete, safe_edit_text
from src.notifications import NotificationEvent, RecipientKind
from src.notifications.context import BookingContext, ReminderContext
from src.notifications.renderer import render
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.paywall import build_upgrade_button_with_fallback
from src.privacy import ConsentRole
from src.rate_limiter import RateLimiter
from src.repositories import ClientNotFound, ClientRepository, MasterNotFound, MasterRepository
from src.repositories.consent import ConsentRepository
from src.repositories.payment_invoice import PaymentInvoiceRepository
from src.schemas import MasterUpdate
from src.schemas.enums import Timezone
from src.settings import get_settings
from src.texts import billing as billing_txt, common as common_txt, master_settings as txt, personal_data as pd_txt
from src.texts.buttons import btn_back, btn_close
from src.use_cases.entitlements import EntitlementsService
from src.user_context import ActiveRole, UserContextStorage
from src.utils import answer_tracked, cleanup_messages, format_work_days_label, track_message, validate_phone

router = Router(name=__name__)
ev = EventLogger(__name__)

SETTINGS_CB_PREFIX = "m:settings:"
_BILLING_CHECK_PREFIX = "billing:pro:check:"
_GUIDE_PAGE_PREFIX = f"{SETTINGS_CB_PREFIX}guide:"
_GUIDE_DEMO_CB = f"{SETTINGS_CB_PREFIX}guide_demo"
_GUIDE_DEMO_PREFIX = f"{SETTINGS_CB_PREFIX}guide_demo:"
_GUIDE_NOTIFICATIONS_PAGE = 3

SETTINGS_BUCKET = "master_settings"
SETTINGS_MAIN_KEY = "master_settings_main"
SETTINGS_VIEW_KEY = "master_settings_view"

VIEW_HUB = "hub"
VIEW_EDIT_PROFILE = "edit_profile"


class MasterSettingsStates(StatesGroup):
    edit_name = State()
    edit_phone = State()
    edit_work_days = State()
    edit_work_time = State()
    edit_slot_size = State()


_NAME_MAX_LEN = 64


def _normalize_name(raw: str | None) -> str:
    return " ".join((raw or "").split()).strip()


def _kb_settings_hub() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=txt.btn_edit_profile(), callback_data=f"{SETTINGS_CB_PREFIX}edit_profile"))
    builder.row(
        InlineKeyboardButton(text=txt.btn_tariffs(), callback_data=f"{SETTINGS_CB_PREFIX}tariffs"),
        InlineKeyboardButton(text=txt.btn_guide(), callback_data=f"{SETTINGS_CB_PREFIX}guide"),
    )
    builder.row(InlineKeyboardButton(text=txt.btn_delete_data(), callback_data=f"{SETTINGS_CB_PREFIX}delete_data"))
    builder.row(InlineKeyboardButton(text=btn_close(), callback_data=f"{SETTINGS_CB_PREFIX}back"))
    return builder.as_markup()


def _kb_settings_edit_profile(
    *,
    notify_clients: bool,
    notify_attendance: bool,
    plan_is_pro: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=txt.btn_name(), callback_data=f"{SETTINGS_CB_PREFIX}name"),
        InlineKeyboardButton(text=txt.btn_phone(), callback_data=f"{SETTINGS_CB_PREFIX}phone"),
        InlineKeyboardButton(text=txt.btn_timezone(), callback_data=f"{SETTINGS_CB_PREFIX}tz"),
    )
    builder.row(
        InlineKeyboardButton(text=txt.btn_work_days(), callback_data=f"{SETTINGS_CB_PREFIX}work_days"),
        InlineKeyboardButton(text=txt.btn_work_time(), callback_data=f"{SETTINGS_CB_PREFIX}work_time"),
    )
    builder.row(
        InlineKeyboardButton(text=txt.btn_slot_size(), callback_data=f"{SETTINGS_CB_PREFIX}slot_size"),
    )
    builder.row(
        InlineKeyboardButton(
            text=txt.btn_notify(notify_clients=notify_clients, plan_is_pro=plan_is_pro),
            callback_data=f"{SETTINGS_CB_PREFIX}notify",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=txt.btn_notify_attendance(notify_attendance=notify_attendance, plan_is_pro=plan_is_pro),
            callback_data=f"{SETTINGS_CB_PREFIX}notify_attendance",
        ),
    )
    builder.row(InlineKeyboardButton(text="↩️ К настройкам", callback_data=f"{SETTINGS_CB_PREFIX}back_menu"))
    builder.row(InlineKeyboardButton(text=btn_close(), callback_data=f"{SETTINGS_CB_PREFIX}back"))
    return builder.as_markup()


def _kb_delete_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Удалить", callback_data=f"{SETTINGS_CB_PREFIX}delete_confirm")],
            [InlineKeyboardButton(text="↩️ К настройкам", callback_data=f"{SETTINGS_CB_PREFIX}back_menu")],
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


def _kb_tariffs_waiting_invoice(
    *,
    contact: str,
    invoice_id: int,
    invoice_url: str | None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if invoice_url:
        rows.append([InlineKeyboardButton(text=billing_txt.btn_pay(), url=invoice_url)])

    rows.append(
        [
            InlineKeyboardButton(
                text=billing_txt.btn_check(),
                callback_data=f"{_BILLING_CHECK_PREFIX}{int(invoice_id)}",
            ),
        ],
    )
    rows.append(
        [
            build_upgrade_button_with_fallback(
                contact=contact,
                text=billing_txt.btn_contact(),
                callback_data="paywall:contact",
            ),
        ],
    )
    rows.append([InlineKeyboardButton(text=btn_back(), callback_data=f"{SETTINGS_CB_PREFIX}back_menu")])
    rows.append([InlineKeyboardButton(text=btn_close(), callback_data="m:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _guide_pages(*, plan_is_pro: bool) -> list[str]:
    pro_line = "✅ (Pro)" if plan_is_pro else "🔒 (только Pro)"
    return [
        (
            "<b>📘 Краткое руководство</b>\n\n"
            "<b>1) Клиенты</b>\n\n"
            "В BeautyDesk есть два типа клиентов:\n"
            "• <b>Telegram-клиент</b> — клиент пишет боту и может получать уведомления о записи.\n"
            "• <b>Офлайн-клиент</b> — клиент без Telegram (только имя и телефон). Отмечен значком 📵\n"
            "  Уведомления офлайн-клиентам не отправляются.\n\n"
            "<b>Совет:</b> офлайн-клиента удобно вести для истории, быстрых записей и последующей привязки Telegram."
        ),
        (
            "<b>2) Как добавить клиента</b>\n\n"
            "• Если клиент есть в Telegram — отправь ему приглашение (ссылку), и он зарегистрируется сам.\n"
            "• Если клиент без Telegram — добавь офлайн-клиента (имя и телефон) и веди записи по нему.\n"
            "  Когда клиент появится в Telegram — отправь приглашение: "
            "бот привяжет его к карточке, если номер телефона совпадёт.\n\n"
            "<b>Важно:</b> автоуведомления доступны <b>только</b> Telegram-клиентам."
        ),
        (
            "<b>3) Как создать запись</b>\n\n"
            "Создать запись можно только если у тебя есть хотя бы один клиент.\n\n"
            "Есть два способа:\n"
            "• <b>Главное меню → Добавить запись</b> → найти клиента → выбрать дату → выбрать время → подтвердить.\n"
            "• <b>Клиенты</b> → открыть карточку клиента → <b>Записать клиента</b>.\n"
            "  (Можно выбрать клиента из списка или через поиск по имени/телефону.)"
        ),
        (
            "<b>4) Расписание</b>\n\n"
            "В <b>Расписании</b> ты можешь:\n"
            "• посмотреть ближайшие записи и историю;\n"
            "• отменить запись;\n"
            f"• перенести запись {pro_line};\n"
            "• отметить явку по прошедшей записи (в течение 7 дней).\n"
            f"• получать напоминание отметить явку, если ты ещё не отметил {pro_line}.\n\n"
            "<b>Совет:</b> отмечать явку важно — в карточке клиента будет видно, "
            "приходил ли он на записи, и сколько было неявок."
        ),
        (
            "<b>5) Уведомление клиентов</b>\n\n"
            f"Автонапоминания клиентам: {pro_line}\n\n"
            "В Pro бот может:\n"
            "• отправлять подтверждение/перенос/отмену записи клиенту;\n"
            "• напоминать о записи за 24 часа и за 2 часа;\n"
            "• отправить «спасибо за визит» после записи.\n\n"
            "Нажми «🧪 Демо уведомлений», чтобы увидеть примеры сообщений "
            "(они придут <b>только</b> тебе в этот чат)."
        ),
    ]


def _kb_guide(*, page: int, total: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{_GUIDE_PAGE_PREFIX}{page - 1}"))
    if page < total - 1:
        nav.append(InlineKeyboardButton(text="➡️ Далее", callback_data=f"{_GUIDE_PAGE_PREFIX}{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="🧪 Демо уведомлений", callback_data=_GUIDE_DEMO_CB)])
    rows.append([InlineKeyboardButton(text="↩️ К настройкам", callback_data=f"{SETTINGS_CB_PREFIX}back_menu")])
    rows.append([InlineKeyboardButton(text=btn_close(), callback_data="m:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _guide_demo_menu_text() -> str:
    return (
        "<b>🧪 Демо уведомлений</b>\n\n"
        "Выбери пример — я отправлю одно демо-сообщение в этот чат.\n"
        "В демо кнопки не выполняют действий.\n"
        "Демо-сообщение можно закрыть кнопкой «Закрыть»."
    )


def _kb_guide_demo_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Запись подтверждена",
                    callback_data=f"{_GUIDE_DEMO_PREFIX}booking_confirmed",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⏰ Напоминание за 24 часа",
                    callback_data=f"{_GUIDE_DEMO_PREFIX}reminder_24h",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⏳ Напоминание за 2 часа",
                    callback_data=f"{_GUIDE_DEMO_PREFIX}reminder_2h",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💛 Спасибо за визит",
                    callback_data=f"{_GUIDE_DEMO_PREFIX}followup_thanks",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"{_GUIDE_PAGE_PREFIX}{_GUIDE_NOTIFICATIONS_PAGE}",
                ),
            ],
            [InlineKeyboardButton(text=btn_close(), callback_data="m:close")],
        ],
    )


def _kb_demo_message() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Закрыть", callback_data="demo:close")],
        ],
    )


def _kb_demo_booking_created_confirmed(*, master_telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Написать мастеру",
                    url=f"tg://user?id={int(master_telegram_id)}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить запись",
                    callback_data="demo:cancel_booking",
                ),
            ],
            [InlineKeyboardButton(text="Закрыть", callback_data="demo:close")],
        ],
    )


def _guide_text(*, pages: list[str], page: int) -> str:
    total = len(pages)
    idx = max(0, min(int(page), total - 1))
    return f"{pages[idx]}\n\n<i>{idx + 1}/{total}</i>"


async def _load_latest_waiting_invoice(*, master_id: int):
    now_utc = datetime.now(UTC)
    async with session_local() as session:
        repo = PaymentInvoiceRepository(session)
        invoice = await repo.get_latest_waiting_for_master(master_id=int(master_id))
        if invoice is None:
            return None
        if invoice.expires_at is not None and invoice.expires_at <= now_utc:
            return None
        return invoice


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
    rows.append([InlineKeyboardButton(text=btn_back(), callback_data=f"{SETTINGS_CB_PREFIX}edit_profile")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _plan_label(plan) -> str:
    source = str(getattr(plan, "source", "free"))
    if source == "trial":
        return "Pro (trial)"
    if source == "paid":
        return "Pro (paid)"
    return "Pro" if bool(getattr(plan, "is_pro", False)) else "Free"


def _render(*, master_name: str, tz: Timezone, plan_label: str) -> str:
    return txt.render_main(
        master_name=master_name,
        plan_label=plan_label,
        tz_value=str(tz.value),
    )


def _render_details(*, master, plan) -> str:
    work_days = format_work_days_label(list(getattr(master, "work_days", []) or [])) or common_txt.placeholder_empty()
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
    notify_attendance = bool(getattr(master, "notify_attendance", True))
    plan_label = _plan_label(plan)

    return _render(
        master_name=master.name,
        tz=master.timezone,
        plan_label=plan_label,
    ) + txt.render_details(
        phone=str(phone),
        work_days=str(work_days),
        work_time=str(work_time),
        slot_size=str(slot_size),
        pro=txt.ProFeaturesView(
            plan_is_pro=bool(plan.is_pro),
            notify_clients=notify_clients,
            notify_attendance=notify_attendance,
        ),
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
        reply_markup=_kb_settings_hub(),
        parse_mode="HTML",
    )
    await state.update_data(
        **{
            SETTINGS_MAIN_KEY: {
                "chat_id": settings_msg.chat.id,
                "message_id": settings_msg.message_id,
            },
            SETTINGS_VIEW_KEY: VIEW_HUB,
        },
    )


async def _refresh_settings_message(*, state: FSMContext, bot, telegram_id: int) -> bool:
    data = await state.get_data()
    main = data.get(SETTINGS_MAIN_KEY) or {}
    view = data.get(SETTINGS_VIEW_KEY) or VIEW_HUB
    chat_id = main.get("chat_id") or telegram_id
    message_id = main.get("message_id")
    if message_id is None:
        return False
    master, plan = await _load_master_and_plan(telegram_id)
    if view == VIEW_EDIT_PROFILE:
        kb = _kb_settings_edit_profile(
            notify_clients=bool(getattr(master, "notify_clients", True)),
            notify_attendance=bool(getattr(master, "notify_attendance", True)),
            plan_is_pro=plan.is_pro,
        )
    else:
        kb = _kb_settings_hub()
    await safe_bot_edit_message_text(
        bot,
        chat_id=int(chat_id),
        message_id=int(message_id),
        text=_render_details(master=master, plan=plan),
        reply_markup=kb,
        parse_mode="HTML",
        event="master.settings.refresh_failed",
    )
    return True


@router.callback_query(UserRole(ActiveRole.MASTER), F.data.startswith(SETTINGS_CB_PREFIX))
async def settings_callbacks(  # noqa: C901, PLR0911, PLR0912, PLR0914, PLR0915
    callback: CallbackQuery,
    state: FSMContext,
    user_ctx_storage: UserContextStorage,
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
        await cleanup_messages(state, callback.bot, bucket=SETTINGS_BUCKET)
        await state.set_state(None)
        await state.update_data(**{SETTINGS_MAIN_KEY: {}, SETTINGS_VIEW_KEY: VIEW_HUB})
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
        await callback.answer()
        await cleanup_messages(state, callback.bot, bucket=SETTINGS_BUCKET)
        await state.set_state(None)
        await state.update_data(**{SETTINGS_VIEW_KEY: VIEW_EDIT_PROFILE})
        await _refresh_settings_message(state=state, bot=callback.bot, telegram_id=telegram_id)
        return

    if data == f"{SETTINGS_CB_PREFIX}back_menu":
        await callback.answer()
        await state.update_data(**{SETTINGS_VIEW_KEY: VIEW_HUB})
        await _refresh_settings_message(state=state, bot=callback.bot, telegram_id=telegram_id)
        return

    if data == f"{SETTINGS_CB_PREFIX}edit_profile":
        await callback.answer()
        await state.update_data(**{SETTINGS_VIEW_KEY: VIEW_EDIT_PROFILE})
        await _refresh_settings_message(state=state, bot=callback.bot, telegram_id=telegram_id)
        return

    if data == f"{SETTINGS_CB_PREFIX}delete_data":
        await callback.answer()
        ev.info("pd.delete_prompt_shown", role="master")
        if callback.message is not None:
            await safe_edit_text(
                callback.message,
                text=pd_txt.delete_master_warning(),
                reply_markup=_kb_delete_confirm(),
                parse_mode="HTML",
                ev=ev,
                event="master.settings.delete_prompt_failed",
            )
        return

    if data == f"{SETTINGS_CB_PREFIX}delete_confirm":
        await callback.answer()
        ev.info("pd.delete_confirmed", role="master")
        async with active_session() as session:
            deleted = await MasterRepository(session).delete_by_telegram_id(telegram_id)
            await ConsentRepository(session).delete_consent(telegram_id=telegram_id, role=str(ConsentRole.MASTER.value))
            await ClientRepository(session).delete_orphan_offline_clients()
            client_exists = True
            try:
                await ClientRepository(session).get_by_telegram_id(telegram_id)
            except ClientNotFound:
                client_exists = False

        if callback.message is not None:
            await safe_delete(callback.message, event="master.settings.delete_main_failed")
        await cleanup_messages(state, callback.bot, bucket=SETTINGS_BUCKET)
        await state.clear()

        if client_exists:
            await user_ctx_storage.set_role(telegram_id, ActiveRole.CLIENT)
        else:
            await user_ctx_storage.clear_role(telegram_id)

        if deleted:
            ev.info("pd.deleted", role="master", deleted=True)
            await callback.bot.send_message(chat_id=telegram_id, text=pd_txt.deleted_done(), parse_mode="HTML")
        else:
            ev.info("pd.deleted", role="master", deleted=False)
            await callback.bot.send_message(chat_id=telegram_id, text=common_txt.context_lost(), parse_mode="HTML")
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

        waiting = await _load_latest_waiting_invoice(master_id=int(master.id))
        if waiting is not None:
            msg = f"{msg}\n\n{billing_txt.pro_waiting_invoice_notice()}"

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
                reply_markup=(
                    _kb_tariffs_waiting_invoice(
                        contact=contact,
                        invoice_id=int(waiting.id),
                        invoice_url=waiting.invoice_url,
                    )
                    if waiting is not None
                    else _kb_tariffs(
                        contact=contact,
                        primary_callback_data=primary_cb,
                        primary_text=primary_text,
                        secondary_text=secondary_text,
                    )
                ),
                parse_mode="HTML",
                ev=ev,
                event="master.settings.tariffs_edit_failed",
            )
        return

    if data == f"{SETTINGS_CB_PREFIX}guide" or data.startswith(_GUIDE_PAGE_PREFIX):
        await callback.answer()
        pages = _guide_pages(plan_is_pro=plan.is_pro)
        page = 0
        if data.startswith(_GUIDE_PAGE_PREFIX):
            raw = data.removeprefix(_GUIDE_PAGE_PREFIX)
            try:
                page = int(raw)
            except ValueError:
                page = 0
        total = len(pages)
        await safe_edit_text(
            callback.message,
            text=_guide_text(pages=pages, page=page),
            reply_markup=_kb_guide(page=page, total=total),
            parse_mode="HTML",
            ev=ev,
            event="master.settings.guide_edit_failed",
        )
        return

    if data == _GUIDE_DEMO_CB:
        await callback.answer()
        await safe_edit_text(
            callback.message,
            text=_guide_demo_menu_text(),
            reply_markup=_kb_guide_demo_menu(),
            parse_mode="HTML",
            ev=ev,
            event="master.settings.guide_demo_edit_failed",
        )
        return

    if data.startswith(_GUIDE_DEMO_PREFIX):
        await callback.answer()

        key = data.removeprefix(_GUIDE_DEMO_PREFIX)
        booking_ctx = BookingContext(
            booking_id=0,
            master_name=str(master.name),
            client_name="Аня (демо)",
            slot_str="10.01.2026 14:00",
            duration_min=60,
        )
        reminder_ctx = ReminderContext(
            master_name=str(master.name),
            slot_str="10.01.2026 14:00",
        )

        demo_map: dict[str, tuple[NotificationEvent, RecipientKind, object]] = {
            "booking_confirmed": (NotificationEvent.BOOKING_CREATED_CONFIRMED, RecipientKind.CLIENT, booking_ctx),
            "reminder_24h": (NotificationEvent.REMINDER_24H, RecipientKind.CLIENT, reminder_ctx),
            "reminder_2h": (NotificationEvent.REMINDER_2H, RecipientKind.CLIENT, reminder_ctx),
            "followup_thanks": (NotificationEvent.FOLLOWUP_THANK_YOU, RecipientKind.CLIENT, reminder_ctx),
        }

        selected = demo_map.get(key)
        if selected is None:
            await callback.answer("Неизвестный демо-тип.", show_alert=True)
            return

        event, recipient, ctx = selected
        rendered = render(event=event, recipient=recipient, context=ctx, reply_markup=None)
        if key == "booking_confirmed":
            markup = _kb_demo_booking_created_confirmed(master_telegram_id=int(telegram_id))
        else:
            markup = _kb_demo_message()
        await callback.bot.send_message(
            chat_id=telegram_id,
            text="<b>🧪 ДЕМО (сообщение клиенту)</b>\n\n" + rendered.text,
            parse_mode="HTML",
            reply_markup=markup,
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
                    [InlineKeyboardButton(text=btn_back(), callback_data=f"{SETTINGS_CB_PREFIX}cancel_edit")],
                ],
            ),
        )
        return

    if data == f"{SETTINGS_CB_PREFIX}name":
        await callback.answer()
        await state.set_state(MasterSettingsStates.edit_name)
        await answer_tracked(
            callback.message,
            state,
            text=txt.ask_new_name(),
            bucket=SETTINGS_BUCKET,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=btn_back(), callback_data=f"{SETTINGS_CB_PREFIX}cancel_edit")],
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
                    [InlineKeyboardButton(text=btn_back(), callback_data=f"{SETTINGS_CB_PREFIX}cancel_edit")],
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
                    [InlineKeyboardButton(text=btn_back(), callback_data=f"{SETTINGS_CB_PREFIX}cancel_edit")],
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
                    [InlineKeyboardButton(text=btn_back(), callback_data=f"{SETTINGS_CB_PREFIX}cancel_edit")],
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

        await callback.answer(text=common_txt.saved())
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

        await callback.answer(common_txt.saved())
        await _refresh_settings_message(state=state, bot=callback.bot, telegram_id=telegram_id)
        return

    if data == f"{SETTINGS_CB_PREFIX}notify_attendance":
        if not plan.is_pro:
            await callback.answer(txt.notify_attendance_pro_only(), show_alert=True)
            return

        current = bool(getattr(master, "notify_attendance", True))
        new_value = not current
        async with active_session() as session:
            master_repo = MasterRepository(session)
            await master_repo.update_by_id(master.id, MasterUpdate(notify_attendance=new_value))

        await callback.answer(common_txt.saved())
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
    await answer_tracked(message, state, text=common_txt.saved(), bucket=SETTINGS_BUCKET)


@router.message(UserRole(ActiveRole.MASTER), StateFilter(MasterSettingsStates.edit_name))
async def save_name(message: Message, state: FSMContext) -> None:
    bind_log_context(flow="master_settings", step="edit_name_save")
    await track_message(state, message, bucket=SETTINGS_BUCKET)
    telegram_id = message.from_user.id
    name = _normalize_name(message.text)
    if not name:
        ev.debug("master_settings.input_invalid", field="name", reason="empty")
        await message.answer(txt.invalid_name(), parse_mode="HTML")
        return
    if len(name) > _NAME_MAX_LEN:
        ev.debug("master_settings.input_invalid", field="name", reason="too_long", len=len(name), max_len=_NAME_MAX_LEN)
        await message.answer(txt.name_too_long(max_len=_NAME_MAX_LEN), parse_mode="HTML")
        return

    async with active_session() as session:
        master_repo = MasterRepository(session)
        master = await master_repo.get_by_telegram_id(telegram_id)
        await master_repo.update_by_id(master.id, MasterUpdate(name=name))

    ev.info("master_settings.name_updated")
    await cleanup_messages(state, message.bot, bucket=SETTINGS_BUCKET)
    await state.set_state(None)
    updated = await _refresh_settings_message(state=state, bot=message.bot, telegram_id=telegram_id)
    if not updated:
        await open_master_settings(message, state)
    await answer_tracked(message, state, text=common_txt.saved(), bucket=SETTINGS_BUCKET)


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
    await answer_tracked(message, state, text=common_txt.saved(), bucket=SETTINGS_BUCKET)


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
    await answer_tracked(message, state, text=common_txt.saved(), bucket=SETTINGS_BUCKET)


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
    await answer_tracked(message, state, text=common_txt.saved(), bucket=SETTINGS_BUCKET)
