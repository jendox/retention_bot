from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from src.core.sa import active_session, session_local
from src.filters.admin import AdminOnly
from src.plans import (
    FREE_BOOKING_HORIZON_DAYS,
    FREE_BOOKINGS_PER_MONTH_LIMIT,
    FREE_CLIENTS_LIMIT,
    PRO_BOOKING_HORIZON_DAYS,
)
from src.repositories import MasterNotFound, MasterRepository, SubscriptionRepository
from src.security.master_invites import create_master_invite_token, encode_master_invite_for_start
from src.settings import get_settings
from src.texts import admin as txt
from src.use_cases.entitlements import EntitlementsService

logger = logging.getLogger(__name__)
router = Router(name=__name__)


def _billing_contact() -> str:
    return get_settings().billing.contact


def _format_dt(dt: datetime | None) -> str:
    if dt is None:
        return txt.placeholder_empty()
    return dt.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC")


def _render_plan_text(
    *,
    title: str,
    plan,
    usage,
    horizon_days: int,
) -> str:
    status = txt.status_label(is_pro=bool(plan.is_pro), source=getattr(plan, "source", None))

    until = _format_dt(plan.active_until)
    clients_limit = "∞" if plan.is_pro else str(FREE_CLIENTS_LIMIT)
    bookings_limit = "∞" if plan.is_pro else str(FREE_BOOKINGS_PER_MONTH_LIMIT)

    return txt.render_plan_text(
        title=title,
        status=status,
        until=until,
        clients_current=int(usage.clients_count),
        clients_limit=clients_limit,
        bookings_current=int(usage.bookings_created_this_month),
        bookings_limit=bookings_limit,
        horizon_days=int(horizon_days),
        billing_contact=_billing_contact(),
    )


@router.message(AdminOnly(), Command("grant_pro"))
async def grant_pro(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if len(parts) != 2:  # noqa: PLR2004
        await message.answer(txt.usage_grant_pro())
        return
    try:
        master_telegram_id = int(parts[0])
        days = int(parts[1])
    except ValueError:
        await message.answer(txt.args_must_be_numbers_grant())
        return
    if days <= 0:
        await message.answer(txt.days_must_be_positive())
        return

    paid_until = datetime.now(UTC) + timedelta(days=days)
    async with active_session() as session:
        master_repo = MasterRepository(session)
        subs_repo = SubscriptionRepository(session)
        try:
            master = await master_repo.get_by_telegram_id(master_telegram_id)
        except MasterNotFound:
            await message.answer(txt.master_not_found())
            return
        await subs_repo.grant_pro(master.id, paid_until)

    logger.info("admin.grant_pro", extra={"master_id": master.id, "paid_until": paid_until.isoformat()})
    await message.answer(txt.pro_activated(master_name=master.name, master_telegram_id=master_telegram_id, until=_format_dt(paid_until)))


@router.message(AdminOnly(), Command("revoke_pro"))
async def revoke_pro(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if len(parts) != 1:  # noqa: PLR2004
        await message.answer(txt.usage_revoke_pro())
        return
    try:
        master_telegram_id = int(parts[0])
    except ValueError:
        await message.answer(txt.master_id_must_be_number())
        return

    async with active_session() as session:
        master_repo = MasterRepository(session)
        subs_repo = SubscriptionRepository(session)
        try:
            master = await master_repo.get_by_telegram_id(master_telegram_id)
        except MasterNotFound:
            await message.answer(txt.master_not_found())
            return
        changed = await subs_repo.revoke_pro(master.id)

    await message.answer(txt.pro_revoked(changed=bool(changed)))


@router.message(
    AdminOnly(),
    F.text.regexp(r"^/plan(?:@\w+)?\s+"),
    Command("plan"),
)
async def admin_plan(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if len(parts) != 1:  # noqa: PLR2004
        await message.answer(txt.usage_plan())
        return
    try:
        master_telegram_id = int(parts[0])
    except ValueError:
        await message.answer(txt.master_id_must_be_number())
        return

    async with session_local() as session:
        master_repo = MasterRepository(session)
        entitlements = EntitlementsService(session)
        try:
            master = await master_repo.get_by_telegram_id(master_telegram_id)
        except MasterNotFound:
            await message.answer(txt.master_not_found())
            return

        plan = await entitlements.get_plan(master_id=master.id)
        usage = await entitlements.get_usage(master_id=master.id)
        horizon = await entitlements.max_booking_horizon_days(master_id=master.id)

    await message.answer(
        _render_plan_text(
            title=txt.title_master_plan(master_name=master.name),
            plan=plan,
            usage=usage,
            horizon_days=horizon,
        ),
    )


@router.message(Command("plan"))
async def my_plan(message: Message) -> None:
    telegram_id = message.from_user.id if message.from_user else None
    if telegram_id is None:
        return

    async with session_local() as session:
        master_repo = MasterRepository(session)
        entitlements = EntitlementsService(session)
        try:
            master = await master_repo.get_by_telegram_id(telegram_id)
        except MasterNotFound:
            await message.answer(txt.master_only())
            return

        plan = await entitlements.get_plan(master_id=master.id)
        usage = await entitlements.get_usage(master_id=master.id)
        horizon = await entitlements.max_booking_horizon_days(master_id=master.id)

    # Override horizon text for Free/Pro to keep it explicit.
    if plan.is_pro and horizon != PRO_BOOKING_HORIZON_DAYS:
        horizon = PRO_BOOKING_HORIZON_DAYS
    if not plan.is_pro and horizon != FREE_BOOKING_HORIZON_DAYS:
        horizon = FREE_BOOKING_HORIZON_DAYS

    await message.answer(
        _render_plan_text(
            title=txt.title_my_plan(),
            plan=plan,
            usage=usage,
            horizon_days=horizon,
        ),
    )


@router.message(AdminOnly(), Command("invite_master"))
async def invite_master(message: Message, command: CommandObject) -> None:
    settings = get_settings()
    secret = settings.security.master_invite_secret
    if secret is None:
        await message.answer(txt.invite_master_secret_missing())
        return

    ttl_hours = settings.security.master_invite_ttl_sec // 3600
    if command.args:
        try:
            ttl_hours = int((command.args or "").strip())
        except ValueError:
            await message.answer(txt.usage_invite_master())
            return
        if ttl_hours <= 0:
            await message.answer(txt.invite_master_bad_ttl())
            return

    token = create_master_invite_token(secret=secret.get_secret_value(), ttl_sec=ttl_hours * 3600)
    link = f"https://t.me/{settings.telegram.bot_username}?start=m_{encode_master_invite_for_start(token)}"
    await message.answer(txt.invite_master_created(link=link, ttl_hours=ttl_hours))
