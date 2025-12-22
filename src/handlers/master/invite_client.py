import logging
from enum import StrEnum

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from src.core.sa import active_session
from src.repositories import MasterRepository
from src.texts import common as common_txt, master_invite_client as txt
from src.texts.buttons import btn_cancel
from src.use_cases.create_client_invite import CreateClientInvite
from src.use_cases.entitlements import EntitlementsService
from src.utils import answer_tracked, cleanup_messages

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
            [InlineKeyboardButton(text=txt.btn_link_only(), callback_data=f"m:invite:{InviteMessageType.LINK_ONLY}")],
            [InlineKeyboardButton(text=txt.btn_friendly(),
                                  callback_data=f"m:invite:{InviteMessageType.FRIENDLY}")],
            [InlineKeyboardButton(text=txt.btn_formal(), callback_data=f"m:invite:{InviteMessageType.FORMAL}")],
            [InlineKeyboardButton(text=btn_cancel(), callback_data="m:invite:cancel")],
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


async def start_invite_client(callback: CallbackQuery, state: FSMContext) -> None:
    telegram_id = callback.from_user.id
    async with active_session() as session:
        master_repo = MasterRepository(session)
        master = await master_repo.get_by_telegram_id(telegram_id)

        entitlements = EntitlementsService(session)
        check = await entitlements.can_attach_client(master_id=master.id)
        if not check.allowed:
            await answer_tracked(
                callback.message,
                state,
                text=txt.quota_reached(current=check.current, limit=check.limit),
                bucket=INVITE_CLIENT_BUCKET,
            )
            return

        use_case = CreateClientInvite(session)
        result = await use_case.execute_for_telegram(master_telegram_id=telegram_id)

    await state.update_data(
        invite_link=result.link,
        master_name=result.master_name,
    )

    warning = ""
    if check.limit is not None and check.current >= int(check.limit * 0.8):  # noqa: PLR2004
        warning = txt.warn_near_limit(current=check.current, limit=int(check.limit))

    await answer_tracked(
        callback.message,
        state,
        text=txt.invite_created(warning=warning),
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
            text=txt.cancelled_hint(),
            show_alert=True,
        )
        return

    kind = _parse_invite_type(callback)
    if kind is None:
        await callback.answer(text=txt.invalid_format(), show_alert=True)
        return

    data = await state.get_data()
    invite_link: str | None = data.get("invite_link")
    master_name: str | None = data.get("master_name")

    if not invite_link or not master_name:
        await callback.answer(text=common_txt.generic_error(), show_alert=True)
        return

    await callback.answer()
    text = txt.render_invite_message(kind=str(kind.value), master_name=master_name, invite_link=invite_link)

    # Убираем клавиатуру выбора формата (редактируем сообщение)
    await callback.message.edit_text(
        txt.done_copy_prompt(),
    )

    await callback.message.answer(text)
    await cleanup_messages(state, callback.bot, bucket=INVITE_CLIENT_BUCKET)
    await state.clear()
