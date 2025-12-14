import logging
from enum import StrEnum

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.sa import session_local
from src.use_cases.create_client_invite import CreateClientInvite
from src.utils import answer_tracked, cleanup_messages, track_message

logger = logging.getLogger(__name__)
router = Router(name=__name__)

INVITE_CLIENT_BUCKET = "client_invite"


class InviteMessageType(StrEnum):
    LINK_ONLY = "link"
    FRIENDLY = "friendly"
    FORMAL = "formal"


class MasterInviteClient(StatesGroup):
    choosing_format = State()


def _build_invite_format_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Только ссылка", callback_data=f"m:invite:{InviteMessageType.LINK_ONLY}")],
            [InlineKeyboardButton(text="💬 Текст (дружелюбный)",
                                  callback_data=f"m:invite:{InviteMessageType.FRIENDLY}")],
            [InlineKeyboardButton(text="📝 Текст (официальный)", callback_data=f"m:invite:{InviteMessageType.FORMAL}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="m:invite:cancel")],
        ],
    )


def _parse_invite_type(callback: CallbackQuery) -> InviteMessageType | None:
    parts = (callback.data or "").split(":")
    try:
        if len(parts) != 3:  # noqa: PLR2004
            raise ValueError()
        kind_raw = parts[2]
        return InviteMessageType(kind_raw)
    except ValueError:
        return None


def render_invite_message(
    *,
    kind: InviteMessageType,
    master_name: str,
    invite_link: str,
) -> str:
    if kind == InviteMessageType.LINK_ONLY:
        return (
            "🔗 Ссылка для клиента:\n\n"
            f"{invite_link}"
        )

    if kind == InviteMessageType.FRIENDLY:
        return (
            f"Привет! 😊 Это {master_name}\n\n"
            "Хочу пригласить тебя пользоваться удобной записью через BeautyDesk.\n"
            "По ссылке ниже ты сможешь быстро перейти в чат и выбрать время ✨\n"
            "Ничего скачивать не нужно — всё работает прямо в Telegram.\n\n"
            f"<a href='{invite_link}'>🔗 Записаться к мастеру</a>\n\n"
            "Если будут вопросы — пиши 💛"
        )

    return (
        "Здравствуйте.\n\n"
        f"Меня зовут {master_name}. Приглашаю вас воспользоваться системой записи BeautyDesk "
        "для удобного согласования времени визита.\n"
        "Пожалуйста, перейдите по ссылке ниже, чтобы начать онлайн-запись:\n\n"
        f"<a href='{invite_link}'>🔗 Перейти к записи</a>\n\n"
        "Если возникнут вопросы — буду рад помочь."
    )


async def start_invite_client(callback: CallbackQuery, state: FSMContext) -> None:
    telegram_id = callback.from_user.id
    async with session_local() as session:
        async with session.begin():
            use_case = CreateClientInvite(session)
            result = await use_case.execute_for_telegram(master_telegram_id=telegram_id)

    await state.update_data(
        invite_link=result.link,
        master_name=result.master_name,
    )

    await answer_tracked(
        callback.message,
        state,
        text="Готово ✅ Ссылка для клиента создана.\n\n"
             "Что отправить клиенту?",
        bucket=INVITE_CLIENT_BUCKET,
        reply_markup=_build_invite_format_keyboard(),
    )
    await state.set_state(MasterInviteClient.choosing_format)


@router.callback_query(
    StateFilter(MasterInviteClient.choosing_format),
    F.data.startswith("m:invite:"),
)
async def master_invite_choose_format(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.data == "m:invite:cancel":
        await cleanup_messages(state, callback.bot, bucket=INVITE_CLIENT_BUCKET)
        await state.set_state(None)
        await callback.answer(
            text="Окей, отменил. Если нужно — нажми «📨 Пригласить клиента» ещё раз 🙂",
            show_alert=True,
        )
        return

    kind = _parse_invite_type(callback)
    if kind is None:
        await callback.answer(text="Некорректный формат.", show_alert=True)
        return

    data = await state.get_data()
    invite_link: str | None = data.get("invite_link")
    master_name: str | None = data.get("master_name")

    if not invite_link or not master_name:
        await callback.answer(text="Что-то пошло не так, попробуй ещё раз.", show_alert=True)
        return

    await callback.answer()
    text = render_invite_message(kind=kind, master_name=master_name, invite_link=invite_link)

    # Убираем клавиатуру выбора формата (редактируем сообщение)
    await callback.message.edit_text(
        "Готово ✨ Скопируй и отправь клиенту сообщение ниже 👇",
    )

    await callback.message.answer(text)
    await state.clear()
