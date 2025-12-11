import logging
from collections.abc import Sequence
from math import ceil

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from src.core.sa import session_local
from src.repositories import InviteRepository, MasterNotFound, MasterRepository
from src.schemas import Invite
from src.schemas.enums import InviteType
from src.utils import answer_tracked

logger = logging.getLogger(__name__)
router = Router(name=__name__)

PAGE_SIZE = 10
CLIENTS_PAGE_PREFIX = "master_clients_page:"

# ---------- Главная клавиатура мастера ----------

MASTER_MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="📨 Пригласить клиента"),
            KeyboardButton(text="➕ Добавить клиента"),
        ],
        [
            KeyboardButton(text="🗓 Добавить запись"),
            KeyboardButton(text="📅 Расписание"),
        ],
        [
            KeyboardButton(text="👥 Клиенты"),
            KeyboardButton(text="⚙️ Настройки"),
        ],
    ],
    resize_keyboard=True,
    input_field_placeholder="Выберите действие",
)


async def send_master_main_menu(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        text=(
            "Главное меню мастера 💇‍♀️\n\n"
            "Здесь ты можешь:\n"
            "• приглашать и добавлять клиентов\n"
            "• создавать записи\n"
            "• смотреть расписание\n"
            "• управлять настройками\n"
        ),
        reply_markup=MASTER_MAIN_KEYBOARD,
    )


@router.message(F.text == "📨 Пригласить клиента")
async def master_invite_client(message: Message) -> None:
    telegram_id = message.from_user.id
    async with session_local() as session:
        master_repo = MasterRepository(session)
        invite_repo = InviteRepository(session)
        async with session.begin():
            master = await master_repo.get_by_telegram_id(telegram_id)
            invite = Invite(
                type=InviteType.CLIENT,
                master_id=master.id,
            )
            invite = await invite_repo.create(invite)

    invite_link = f"https://t.me/beautydesk_bot?start=c_{invite.token}"
    await message.answer(
        "Чтобы пригласить клиента, отправь ему эту ссылку 👇\n"
        "По ней он быстро зарегистрируется и появится у тебя в списке клиентов.\n"
        "Скачивать ничего не нужно — всё работает прямо в Telegram ✨\n"
        "Ниже будут два варианта сообщения для пересылки — выбери любой и просто отправь клиенту 👇",
    )
    await message.answer(
        f"Привет! 😊 Это {master.name}\n\n"
        "Хочу пригласить тебя пользоваться удобной записью через BeautyDesk.\n"
        "По кнопке ниже ты сможешь быстро перейти в чат и выбрать время ✨\n"
        "Ничего скачивать не нужно — всё работает прямо в Telegram.\n\n"
        f"<a href='{invite_link}'>🔗 Записаться к мастеру</a>\n\n"
        "Если будут вопросы — пиши 💛",
    )
    await message.answer(
        "Здравствуйте.\n\n"
        f"Меня зовут {master.name}. Приглашаю вас воспользоваться системой записи BeautyDesk "
        f"для удобного согласования времени визита.\n"
        "Пожалуйста, перейдите по ссылке ниже, чтобы подтвердить данные и "
        "получить доступ к системе онлайн-записи:\n\n"
        f"<a href='{invite_link}'>🔗 Перейти к записи</a>\n\n"
        "Если возникнут вопросы — буду рад помочь.",
    )


@router.message(F.text == "➕ Добавить клиента")
async def master_add_client(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        "Тут будет флоу добавления клиента вручную ✍️",
    )


@router.message(F.text == "🗓 Добавить запись")
async def master_add_booking(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        "Тут будет создание новой записи 📆",
    )


@router.message(F.text == "📅 Расписание")
async def master_schedule(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        "Здесь в будущем покажем твое расписание на день / неделю 🗓",
    )


@router.message(F.text == "⚙️ Настройки")
async def master_settings(message: Message, state: FSMContext) -> None:
    await answer_tracked(
        message,
        state,
        "Тут будут настройки мастера: график, таймзона, уведомления и т.д. ⚙️",
    )


async def _fetch_master_clients(telegram_id: int) -> Sequence:
    """
    Забираем мастера вместе с клиентами через MasterRepository.get_details_by_telegram_id,
    чтобы не лезть в SQL руками.
    """
    async with session_local() as session:
        repo = MasterRepository(session)
        try:
            master = await repo.get_details_by_telegram_id(telegram_id)
        except MasterNotFound:
            logger.warning("master.not_found_in_clients_menu", extra={"telegram_id": telegram_id})
            return []
        return master.clients  # это уже Pydantic-схемы Client


def _build_clients_page_text(
    clients: Sequence,
    page: int,
    total_pages: int,
) -> str:
    if not clients:
        return "У тебя пока нет клиентов 👀\n\n" "Когда они появятся, ты увидишь их здесь."

    lines: list[str] = [
        f"👥 Клиенты (страница {page}/{total_pages})",
        "",
    ]

    for idx, client in enumerate(clients, start=1):
        # ожидаем, что у Client есть name / phone
        phone = f" — {client.phone}" if getattr(client, "phone", None) else ""
        lines.append(f"{idx}. {client.name}{phone}")

    return "\n".join(lines)


def _build_clients_pagination_keyboard(
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []

    # пагинация только если больше 1 страницы
    if total_pages > 1:
        nav_row: list[InlineKeyboardButton] = []

        if page > 1:
            nav_row.append(
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"{CLIENTS_PAGE_PREFIX}{page - 1}",
                ),
            )

        if page < total_pages:
            nav_row.append(
                InlineKeyboardButton(
                    text="Вперёд ➡️",
                    callback_data=f"{CLIENTS_PAGE_PREFIX}{page + 1}",
                ),
            )

        if nav_row:
            buttons.append(nav_row)

    # кнопка закрытия списка
    buttons.append(
        [
            InlineKeyboardButton(
                text="✖️ Закрыть",
                callback_data=f"{CLIENTS_PAGE_PREFIX}close",
            ),
        ],
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(F.text == "👥 Клиенты")
async def master_clients_entry(message: Message, state: FSMContext) -> None:
    telegram_id = message.from_user.id
    all_clients = await _fetch_master_clients(telegram_id)

    if not all_clients:
        await answer_tracked(
            message,
            state,
            "У тебя пока нет клиентов 👀\n\n"
            "Пригласи клиента по ссылке или добавь вручную, и они появятся здесь.",
        )
        return

    page = 1
    total_pages = max(1, ceil(len(all_clients) / PAGE_SIZE))

    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    clients_page = all_clients[start:end]

    text = _build_clients_page_text(clients_page, page, total_pages)
    keyboard = _build_clients_pagination_keyboard(page, total_pages)

    await answer_tracked(
        message,
        state,
        text=text,
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith(CLIENTS_PAGE_PREFIX))
async def master_clients_pagination(callback: CallbackQuery) -> None:
    await callback.answer()

    data = callback.data[len(CLIENTS_PAGE_PREFIX):]

    # закрытие списка
    if data == "close":
        try:
            await callback.message.delete()
        except Exception:
            # если не получилось удалить — просто убираем клавиатуру
            await callback.message.edit_reply_markup(reply_markup=None)
        return

    # page number
    try:
        page = int(data)
    except ValueError:
        # некорректный колбек — тихо игнорируем
        return

    telegram_id = callback.from_user.id
    all_clients = await _fetch_master_clients(telegram_id)

    if not all_clients:
        await callback.message.edit_text("Клиентов пока нет 👀")
        return

    total_pages = max(1, ceil(len(all_clients) / PAGE_SIZE))
    if page < 1 or page > total_pages:
        # вне допустимого диапазона — игнор
        return

    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    clients_page = all_clients[start:end]

    text = _build_clients_page_text(clients_page, page, total_pages)
    keyboard = _build_clients_pagination_keyboard(page, total_pages)

    await callback.message.edit_text(
        text=text,
        reply_markup=keyboard,
    )
