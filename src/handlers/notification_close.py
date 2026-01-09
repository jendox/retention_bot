from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from src.handlers.shared.guards import rate_limit_callback
from src.handlers.shared.ui import safe_delete, safe_edit_reply_markup
from src.notifications.close import NOTIFICATION_CLOSE_CB
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.rate_limiter import RateLimiter

router = Router(name=__name__)
ev = EventLogger(__name__)


@router.callback_query(F.data == NOTIFICATION_CLOSE_CB)
async def notification_close(
    callback: CallbackQuery,
    state: FSMContext,  # noqa: ARG001 - kept for middleware signature compatibility
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="notification_close", step="close")
    if not await rate_limit_callback(callback, rate_limiter, name="notification_close:close", ttl_sec=1):
        return
    await callback.answer()

    if callback.message is None:
        return
    deleted = await safe_delete(callback.message, ev=ev, event="notification_close.delete_failed")
    if not deleted:
        await safe_edit_reply_markup(
            callback.message,
            reply_markup=None,
            ev=ev,
            event="notification_close.hide_keyboard_failed",
        )
