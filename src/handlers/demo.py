from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from src.handlers.shared.ui import safe_delete
from src.observability.context import bind_log_context
from src.observability.events import EventLogger

router = Router(name=__name__)
ev = EventLogger(__name__)


@router.callback_query(F.data.startswith("demo:"))
async def demo_noop(callback: CallbackQuery) -> None:
    bind_log_context(flow="demo", step="noop")
    ev.info("demo.noop")
    data = callback.data or ""
    if data == "demo:close":
        await callback.answer()
        if callback.message is None:
            return
        deleted = await safe_delete(callback.message, ev=ev, event="demo.close_delete_failed")
        if not deleted:
            await callback.message.edit_reply_markup(reply_markup=None)
        return

    await callback.answer("Демо: действие не выполняется.")
