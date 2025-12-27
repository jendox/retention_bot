from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from src.filters.user_role import UserRole
from src.handlers.master.master_menu import build_master_clients_keyboard
from src.handlers.shared.ui import safe_delete, safe_edit_text
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.settings import get_settings
from src.texts import master_menu as master_menu_txt, paywall as paywall_txt
from src.user_context import ActiveRole

router = Router(name=__name__)
ev = EventLogger(__name__)


@router.callback_query(F.data == "paywall:contact")
async def paywall_contact(callback: CallbackQuery) -> None:
    bind_log_context(flow="paywall", step="contact")
    ev.info("paywall.contact")
    await callback.answer()
    contact = get_settings().billing.contact
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=paywall_txt.contact_message(contact=contact),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "paywall:close")
async def paywall_close(callback: CallbackQuery) -> None:
    bind_log_context(flow="paywall", step="close")
    ev.info("paywall.close")
    await callback.answer()
    if callback.message is None:
        return
    deleted = await safe_delete(callback.message, ev=ev, event="paywall.close_delete_failed")
    if not deleted:
        await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == "paywall:back:clients_menu")
async def paywall_back_clients_menu(callback: CallbackQuery) -> None:
    bind_log_context(flow="paywall", step="back_clients_menu")
    ev.info("paywall.back_clients_menu")
    await callback.answer()
    if callback.message is None:
        return
    await safe_edit_text(
        callback.message,
        text=master_menu_txt.choose_action(),
        reply_markup=build_master_clients_keyboard(),
        parse_mode="HTML",
        ev=ev,
        event="paywall.back_clients_menu_edit_failed",
    )
