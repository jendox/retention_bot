import logging
from datetime import datetime, time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import session_local
from src.handlers.master.master_menu import send_master_main_menu
from src.repositories import MasterRepository
from src.schemas import MasterCreate
from src.schemas.enums import Timezone
from src.utils import answer_tracked, cleanup_messages, track_callback_message, track_message

router = Router(name=__name__)
logger = logging.getLogger(__name__)


class MasterRegistration(StatesGroup):
    name = State()
    work_days = State()
    work_time = State()
    slot_size = State()
    confirm = State()


# ------------ helpers ------------

def parse_work_days(raw: str) -> list[int] | None:
    """
    Парсим дни недели в виде:
    - "1-5" -> [0,1,2,3,4]
    - "1,3,5" -> [0,2,4]
    где 1 = понедельник, 7 = воскресенье.
    В БД храним 0-6.
    """
    text = raw.replace(" ", "")
    if not text:
        return None

    days: list[int] = []

    try:
        if "-" in text and "," not in text:
            start_str, end_str = text.split("-", 1)
            start = int(start_str)
            end = int(end_str)
            if not (1 <= start <= 7 and 1 <= end <= 7 and start <= end):
                return None
            days = [day - 1 for day in range(start, end + 1)]
        else:
            parts = text.split(",")
            for part in parts:
                day = int(part)
                if not (1 <= day <= 7):
                    return None
                days.append(day - 1)

        # убираем дубли и сортируем
        days = sorted(set(days))
        return days or None
    except ValueError:
        return None


def parse_time_range(raw: str) -> tuple[time, time] | None:
    """
    Парсим строку вида "10:00-19:00" в (time(10,0), time(19,0)).
    """
    text = raw.replace(" ", "")
    if "-" not in text:
        return None
    start_str, end_str = text.split("-", 1)
    try:
        start_dt = datetime.strptime(start_str, "%H:%M")
        end_dt = datetime.strptime(end_str, "%H:%M")
    except ValueError:
        return None

    start_t = start_dt.time()
    end_t = end_dt.time()
    if start_t >= end_t:
        return None

    return start_t, end_t


def parse_slot_size(raw: str) -> int | None:
    """
    Ожидаем количество минут. Разумные значения: 15, 20, 30, 45, 60, 90, 120.
    """
    text = raw.strip()
    if not text:
        return None
    try:
        minutes = int(text)
    except ValueError:
        return None

    allowed = {15, 20, 30, 45, 60, 90, 120}
    if minutes not in allowed:
        return None
    return minutes


async def start_master_registration(
    message: Message,
    state: FSMContext,
) -> None:
    await answer_tracked(
        message,
        state,
        "Привет! 👋\n"
        "Давай настроим твой профиль в BeautyDesk.\n\n"
        "Как тебя зовут? (Например: Маша)",
    )
    await state.set_state(MasterRegistration.name)


@router.message(MasterRegistration.name)
async def process_master_name(
    message: Message,
    state: FSMContext,
) -> None:
    await track_message(state, message)
    name = (message.text or "").strip()
    if not name:
        await answer_tracked(
            message,
            state,
            text="Я не понял имя 🤔\n"
                 "Пожалуйста, напиши, как к тебе обращаться. Например: <b>Маша</b>",
        )
        return

    await state.update_data(name=name)

    await answer_tracked(
        message,
        state,
        text=f"Отлично, <b>{name}</b>! ✨\n\n"
             "Теперь давай настроим твои рабочие дни.\n\n"
             "<b>В какие дни недели ты работаешь?</b>\n"
             "Напиши номера дней недели:\n"
             "1 — Пн, 2 — Вт, 3 — Ср, 4 — Чт, 5 — Пт, 6 — Сб, 7 — Вс\n\n"
             "Примеры:\n"
             "• <code>1-5</code> — с понедельника по пятницу\n"
             "• <code>1,3,5</code> — пн, ср, пт",
    )
    await state.set_state(MasterRegistration.work_days)


@router.message(MasterRegistration.work_days)
async def process_master_work_days(
    message: Message,
    state: FSMContext,
) -> None:
    await track_message(state, message)
    work_days = parse_work_days(message.text or "")
    if work_days is None:
        await answer_tracked(
            message,
            state,
            text="Не смог разобрать дни недели 🤔\n\n"
                 "Напиши номера дней недели в одном из форматов:\n"
                 "• <code>1-5</code>\n"
                 "• <code>1,3,5</code>\n\n"
                 "Где 1 — Пн, 7 — Вс.",
        )
        return

    await state.update_data(work_days=work_days)

    await answer_tracked(
        message,
        state,
        text="Принято! ✅\n\n"
             "<b>Твоё рабочее время в течение дня?</b>\n"
             "Напиши в формате <code>HH:MM-HH:MM</code>.\n\n"
             "Например: <code>10:00-19:00</code>",
    )
    await state.set_state(MasterRegistration.work_time)


@router.message(MasterRegistration.work_time)
async def process_master_work_time(
    message: Message,
    state: FSMContext,
) -> None:
    await track_message(state, message)
    parsed = parse_time_range(message.text or "")
    if parsed is None:
        await answer_tracked(
            message,
            state,
            text="Не получилось разобрать время 🕒\n\n"
                 "Напиши, пожалуйста, в формате <code>HH:MM-HH:MM</code>.\n"
                 "Например: <code>10:00-19:00</code>",
        )
        return

    start_time, end_time = parsed
    await state.update_data(start_time=start_time, end_time=end_time)

    await answer_tracked(
        message,
        state,
        text="Супер! ✅\n\n"
             "<b>Какой длительности обычно одна запись?</b>\n"
             "Напиши количество минут.\n\n"
             "Например: <code>30</code>, <code>60</code> или <code>90</code>.",
    )
    await state.set_state(MasterRegistration.slot_size)


@router.message(MasterRegistration.slot_size)
async def process_master_slot_size(
    message: Message,
    state: FSMContext,
) -> None:
    await track_message(state, message)
    slot_size = parse_slot_size(message.text or "")
    if slot_size is None:
        await answer_tracked(
            message,
            state,
            text="Хмм, не похоже на подходящую длительность слота ⏱️\n\n"
                 "Напиши количество минут, например: <code>30</code>, <code>60</code> или <code>90</code>.",
        )
        return

    await state.update_data(slot_size_min=slot_size)
    data = await state.get_data()

    text = (
        "Проверь, пожалуйста, данные 👇\n\n"
        f"<b>Имя:</b> {data['name']}\n"
        f"<b>Рабочие дни:</b> {', '.join(str(day + 1) for day in data['work_days'])}\n"
        f"<b>Время работы:</b> {data['start_time'].strftime('%H:%M')}–{data['end_time'].strftime('%H:%M')}\n"
        f"<b>Длительность слота:</b> {data['slot_size_min']} мин.\n\n"
        "Всё верно?"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Всё верно",
                    callback_data="master_reg_confirm",
                ),
                InlineKeyboardButton(
                    text="🔁 Заполнить заново",
                    callback_data="master_reg_restart",
                ),
            ],
        ],
    )
    await answer_tracked(
        message,
        state,
        text=text,
        reply_markup=keyboard,
    )
    await state.set_state(MasterRegistration.confirm)


@router.callback_query(
    MasterRegistration.confirm,
    F.data == "master_reg_confirm",
)
async def master_reg_confirm(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()
    await track_callback_message(state, callback)
    data = await state.get_data()
    telegram_id = callback.from_user.id

    master_create = MasterCreate(
        telegram_id=telegram_id,
        name=data["name"],
        work_days=data["work_days"],
        start_time=data["start_time"],
        end_time=data["end_time"],
        slot_size_min=data["slot_size_min"],
        timezone=Timezone.EUROPE_MINSK,
    )

    async with session_local() as session:
        async with session.begin():
            repo = MasterRepository(session)
            master = await repo.create(master_create)
            logger.info(
                "master.created",
                extra={"master_id": master.id, "telegram_id": telegram_id},
            )

    await cleanup_messages(state, callback.bot)
    await state.clear()

    await callback.message.answer(
        "Готово! 🎉\n\n"
        "Твой профиль мастера создан.\n"
        "Теперь ты можешь принимать клиентов и вести записи в BeautyDesk.",
    )
    await send_master_main_menu(callback.message, state)


@router.callback_query(
    MasterRegistration.confirm,
    F.data == "master_reg_restart",
)
async def master_reg_restart(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()
    await cleanup_messages(state, callback.bot)
    await state.clear()
    await start_master_registration(callback.message, state)
