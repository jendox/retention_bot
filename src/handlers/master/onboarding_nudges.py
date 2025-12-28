from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from src.core.sa import active_session
from src.filters.user_role import UserRole
from src.handlers.master.add_booking import start_add_booking
from src.handlers.master.add_client import start_add_client
from src.handlers.master.invite_client import start_invite_client
from src.handlers.shared.guards import rate_limit_callback
from src.handlers.shared.ui import safe_delete
from src.notifications.notifier import Notifier
from src.observability.context import bind_log_context
from src.observability.events import EventLogger
from src.rate_limiter import RateLimiter
from src.repositories import MasterRepository
from src.repositories.scheduled_notification import ScheduledNotificationRepository
from src.texts import common as common_txt
from src.user_context import ActiveRole

ev = EventLogger(__name__)
router = Router(name=__name__)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == "m:onb:add_client")
async def onboarding_add_client(
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="master_onboarding", step="add_client")
    if not await rate_limit_callback(callback, rate_limiter, name="master_onboarding:add_client", ttl_sec=2):
        return
    await callback.answer()
    if callback.message is not None:
        await safe_delete(callback.message, ev=ev, event="onboarding.delete_message_failed")
    await start_add_client(callback, state, notifier, rate_limiter)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == "m:onb:invite_client")
async def onboarding_invite_client(
    callback: CallbackQuery,
    state: FSMContext,
    notifier: Notifier,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="master_onboarding", step="invite_client")
    if not await rate_limit_callback(callback, rate_limiter, name="master_onboarding:invite_client", ttl_sec=2):
        return
    await callback.answer()
    if callback.message is not None:
        await safe_delete(callback.message, ev=ev, event="onboarding.delete_message_failed")
    await start_invite_client(callback, state, notifier, rate_limiter)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == "m:onb:add_booking")
async def onboarding_add_booking(
    callback: CallbackQuery,
    state: FSMContext,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="master_onboarding", step="add_booking")
    if not await rate_limit_callback(callback, rate_limiter, name="master_onboarding:add_booking", ttl_sec=2):
        return
    await callback.answer()
    if callback.message is not None:
        await safe_delete(callback.message, ev=ev, event="onboarding.delete_message_failed")
        await start_add_booking(callback.message, state)


@router.callback_query(UserRole(ActiveRole.MASTER), F.data == "m:onb:disable")
async def onboarding_disable(
    callback: CallbackQuery,
    rate_limiter: RateLimiter | None = None,
) -> None:
    bind_log_context(flow="master_onboarding", step="disable")
    if not await rate_limit_callback(callback, rate_limiter, name="master_onboarding:disable", ttl_sec=2):
        return

    if callback.from_user is None:
        await callback.answer(common_txt.generic_error(), show_alert=False)
        return

    telegram_id = int(callback.from_user.id)
    try:
        async with active_session() as session:
            master = await MasterRepository(session).get_by_telegram_id(telegram_id)
            await MasterRepository(session).set_onboarding_nudges_enabled(master_id=int(master.id), enabled=False)
            await ScheduledNotificationRepository(session).cancel_onboarding_for_master(master_id=int(master.id))
    except Exception as exc:
        await ev.aexception("onboarding.disable_failed", exc=exc)
        await callback.answer(common_txt.generic_error(), show_alert=True)
        return

    await callback.answer(common_txt.saved(), show_alert=False)
    if callback.message is not None:
        await safe_delete(callback.message, ev=ev, event="onboarding.delete_message_failed")
