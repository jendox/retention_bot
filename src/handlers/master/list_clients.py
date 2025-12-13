import logging
from collections.abc import Sequence
from math import ceil

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import session_local
from src.repositories import MasterNotFound, MasterRepository
from src.utils import answer_tracked

logger = logging.getLogger(__name__)
router = Router(name=__name__)


PAGE_SIZE = 10
CLIENTS_PAGE_PREFIX = "master_clients_page:"


async def _fetch_master_clients(telegram_id: int) -> Sequence:
    async with session_local() as session:
        repo = MasterRepository(session)
        try:
            master = await repo.get_with_clients_by_telegram_id(telegram_id)
        except MasterNotFound:
            logger.warning("master.not_found_in_clients_menu", extra={"telegram_id": telegram_id})
            return []
        return master.clients


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

    for index, client in enumerate(clients, start=1):
        phone = f" — {client.phone}" if getattr(client, "phone", None) else ""
        lines.append(f"{index}. {client.name}{phone}")

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
            text="У тебя пока нет клиентов 👀\n\n"
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

    data = callback.data.split(":")[1]

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
