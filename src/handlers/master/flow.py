from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from src.observability.events import EventLogger
from src.texts import common as common_txt
from src.utils import cleanup_messages

ev = EventLogger(__name__)


async def context_lost(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    bucket: str,
    reason: str,
) -> None:
    """
    Consistent handler for "state is missing/corrupted" cases in master flows.
    """
    ev.warning("flow.context_lost", flow=bucket, reason=reason)
    await callback.answer(common_txt.context_lost(), show_alert=True)
    await cleanup_messages(state, callback.bot, bucket=bucket)
    await state.clear()
