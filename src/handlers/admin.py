from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from src.core.sa import active_session, session_local
from src.filters.admin import AdminOnly
from src.plans import FREE_BOOKINGS_PER_MONTH_LIMIT, FREE_BOOKING_HORIZON_DAYS, FREE_CLIENTS_LIMIT, PRO_BOOKING_HORIZON_DAYS
from src.repositories import MasterNotFound, MasterRepository, SubscriptionRepository
from src.use_cases.entitlements import EntitlementsService

logger = logging.getLogger(__name__)
router = Router(name=__name__)


def _billing_contact() -> str:
    return os.getenv("BILLING__CONTACT", "@admin")


def _format_dt(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC")


def _render_plan_text(
    *,
    title: str,
    plan,
    usage,
    horizon_days: int,
) -> str:
    if plan.is_pro:
        status = "Pro"
        if plan.source == "trial":
            status += " (trial)"
        elif plan.source == "paid":
            status += " (оплачено)"
    else:
        status = "Free"

    until = _format_dt(plan.active_until)
    clients_limit = "∞" if plan.is_pro else str(FREE_CLIENTS_LIMIT)
    bookings_limit = "∞" if plan.is_pro else str(FREE_BOOKINGS_PER_MONTH_LIMIT)

    return (
        f"{title}\n\n"
        f"<b>Тариф:</b> {status}\n"
        f"<b>Действует до:</b> {until}\n\n"
        f"<b>Клиенты:</b> {usage.clients_count}/{clients_limit}\n"
        f"<b>Новые записи (мес):</b> {usage.bookings_created_this_month}/{bookings_limit}\n"
        f"<b>Горизонт записи:</b> {horizon_days} дней\n\n"
        "Чтобы подключить Pro — напиши: "
        f"{_billing_contact()}"
    )


@router.message(AdminOnly(), Command("grant_pro"))
async def grant_pro(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if len(parts) != 2:  # noqa: PLR2004
        await message.answer("Использование: /grant_pro <master_telegram_id> <days>")
        return
    try:
        master_telegram_id = int(parts[0])
        days = int(parts[1])
    except ValueError:
        await message.answer("Аргументы должны быть числами: /grant_pro <master_telegram_id> <days>")
        return
    if days <= 0:
        await message.answer("days должен быть > 0")
        return

    paid_until = datetime.now(UTC) + timedelta(days=days)
    async with active_session() as session:
        master_repo = MasterRepository(session)
        subs_repo = SubscriptionRepository(session)
        try:
            master = await master_repo.get_by_telegram_id(master_telegram_id)
        except MasterNotFound:
            await message.answer("Мастер не найден.")
            return
        await subs_repo.grant_pro(master.id, paid_until)

    logger.info("admin.grant_pro", extra={"master_id": master.id, "paid_until": paid_until.isoformat()})
    await message.answer(
        f"✅ Pro активирован\n\n<b>Мастер:</b> {master.name} ({master_telegram_id})\n<b>До:</b> {_format_dt(paid_until)}"
    )


@router.message(AdminOnly(), Command("revoke_pro"))
async def revoke_pro(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if len(parts) != 1:  # noqa: PLR2004
        await message.answer("Использование: /revoke_pro <master_telegram_id>")
        return
    try:
        master_telegram_id = int(parts[0])
    except ValueError:
        await message.answer("master_telegram_id должен быть числом.")
        return

    async with active_session() as session:
        master_repo = MasterRepository(session)
        subs_repo = SubscriptionRepository(session)
        try:
            master = await master_repo.get_by_telegram_id(master_telegram_id)
        except MasterNotFound:
            await message.answer("Мастер не найден.")
            return
        changed = await subs_repo.revoke_pro(master.id)

    await message.answer(
        "✅ Pro отключён." if changed else "ℹ️ Подписка не найдена (ничего не изменил)."
    )


@router.message(AdminOnly(), Command("plan"))
async def admin_plan(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if len(parts) != 1:  # noqa: PLR2004
        await message.answer("Использование: /plan <master_telegram_id>")
        return
    try:
        master_telegram_id = int(parts[0])
    except ValueError:
        await message.answer("master_telegram_id должен быть числом.")
        return

    async with session_local() as session:
        master_repo = MasterRepository(session)
        entitlements = EntitlementsService(session)
        try:
            master = await master_repo.get_by_telegram_id(master_telegram_id)
        except MasterNotFound:
            await message.answer("Мастер не найден.")
            return

        plan = await entitlements.get_plan(master_id=master.id)
        usage = await entitlements.get_usage(master_id=master.id)
        horizon = await entitlements.max_booking_horizon_days(master_id=master.id)

    await message.answer(
        _render_plan_text(
            title=f"Тариф мастера 💳\n<b>{master.name}</b>",
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
            await message.answer("Команда доступна мастерам после регистрации.")
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
            title="Твой тариф 💳",
            plan=plan,
            usage=usage,
            horizon_days=horizon,
        ),
    )

