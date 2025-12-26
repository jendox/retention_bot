from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from src.filters.user_role import UserRole
from src.handlers.shared.guards import rate_limit_callback
from src.handlers.shared.ui import safe_delete
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.rate_limiter import RateLimiter
from src.user_context import ActiveRole
from src.utils import cleanup_messages

router = Router(name=__name__)
ev = EventLogger(__name__)

MASTER_CLOSE_CB = "m:close"

_MASTER_CLEANUP_BUCKETS = (
    "master_add_booking",
    "master_add_client",
    "master_edit_client",
    "master_edit_client_card",
    "master_registration",
    "master_reschedule",
    "master_settings",
    "master_workday_overrides",
    # historical naming, but used by master invite flow
    "client_invite",
)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == MASTER_CLOSE_CB)
async def master_close(
    callback: CallbackQuery,
    state: FSMContext,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="master_close", step="close")
    if not await rate_limit_callback(callback, rate_limiter, name="master_close:close", ttl_sec=1):
        return
    await callback.answer()

    for bucket in _MASTER_CLEANUP_BUCKETS:
        await cleanup_messages(state, callback.bot, bucket=bucket)
    await state.clear()

    if callback.message is None:
        return
    deleted = await safe_delete(callback.message, ev=ev, event="master_close.delete_failed")
    if not deleted:
        await callback.message.edit_reply_markup(reply_markup=None)
