from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from redis.asyncio import Redis
from sqlalchemy import or_, select

from src.core.sa import Database, active_session, session_local
from src.integrations.expresspay import ExpressPayClient
from src.models.master import Master as MasterEntity
from src.models.payment_invoice import PaymentInvoice as PaymentInvoiceEntity
from src.observability import setup_logging
from src.observability.events import EventLogger
from src.observability.heartbeat import write_worker_heartbeat
from src.observability.metrics_server import start_metrics_server
from src.repositories.payment_invoice import PaymentInvoiceRepository
from src.settings import AppSettings, app_settings, get_settings
from src.texts import billing as billing_txt
from src.use_cases.check_pro_payment import CheckProPayment, CheckProPaymentRequest
from src.use_cases.entitlements import EntitlementsService

ev = EventLogger("workers.payments")


@dataclass(frozen=True)
class DueInvoice:
    invoice_id: int
    master_id: int
    master_telegram_id: int
    status: str
    paid_notified_at: datetime | None


@dataclass(frozen=True)
class PaymentsLoopConfig:
    pro_days: int
    tick: timedelta
    batch_size: int
    min_recheck: timedelta


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BeautyDesk payments worker (polls ExpressPay invoices).")
    parser.add_argument("--env-file", default=None, help="Env file path (default: ENV_FILE or .env.local)")
    parser.add_argument("--tick-sec", type=int, default=int(os.getenv("PAYMENTS_TICK_SEC", "60")))
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("PAYMENTS_BATCH_SIZE", "50")))
    parser.add_argument(
        "--min-recheck-sec",
        type=int,
        default=int(os.getenv("PAYMENTS_MIN_RECHECK_SEC", "60")),
        help="Min seconds between rechecks of the same invoice",
    )
    return parser.parse_args()


async def _load_due_invoices(
    *,
    now_utc: datetime,
    batch_size: int,
    min_recheck: timedelta,
) -> list[DueInvoice]:
    """
    Returns list of (invoice_id, master_id, master_telegram_id, status, paid_notified_at).
    """
    cutoff = now_utc - min_recheck
    async with session_local() as session:
        stmt = (
            select(
                PaymentInvoiceEntity.id,
                PaymentInvoiceEntity.master_id,
                MasterEntity.telegram_id,
                PaymentInvoiceEntity.status,
                PaymentInvoiceEntity.paid_notified_at,
            )
            .join(MasterEntity, MasterEntity.id == PaymentInvoiceEntity.master_id)
            .where(
                or_(
                    # Normal flow: poll provider for unpaid invoices.
                    (
                        (PaymentInvoiceEntity.status == "waiting")
                        & (or_(PaymentInvoiceEntity.expires_at.is_(None), PaymentInvoiceEntity.expires_at > now_utc))
                        & (
                            or_(
                                PaymentInvoiceEntity.last_checked_at.is_(None),
                                PaymentInvoiceEntity.last_checked_at < cutoff,
                            )
                        )
                    ),
                    # Recovery/testing flow: notify once if invoice is already marked as paid in DB.
                    (
                        (PaymentInvoiceEntity.status == "paid")
                        & (PaymentInvoiceEntity.paid_notified_at.is_(None))
                    ),
                ),
            )
            .order_by(
                PaymentInvoiceEntity.paid_notified_at.asc().nullsfirst(),
                PaymentInvoiceEntity.last_checked_at.asc().nullsfirst(),
                PaymentInvoiceEntity.id.asc(),
            )
            .limit(int(batch_size))
        )
        rows = (await session.execute(stmt)).all()
        return [
            DueInvoice(
                invoice_id=int(invoice_id),
                master_id=int(master_id),
                master_telegram_id=int(master_telegram_id),
                status=str(status),
                paid_notified_at=paid_notified_at,
            )
            for invoice_id, master_id, master_telegram_id, status, paid_notified_at in rows
        ]


async def _notify_paid_if_needed(
    *,
    session,
    bot: Bot,
    invoice_id: int,
    master_id: int,
    master_telegram_id: int,
) -> bool:
    plan = await EntitlementsService(session).get_plan(master_id=int(master_id))
    await bot.send_message(
        chat_id=master_telegram_id,
        text=billing_txt.pro_paid_message(paid_until=plan.active_until),
        parse_mode="HTML",
    )
    return await PaymentInvoiceRepository(session).mark_paid_notified(
        int(invoice_id),
        at=datetime.now(UTC),
    )


async def _process_due_invoice(
    *,
    session,
    bot: Bot,
    express_pay_client: ExpressPayClient,
    pro_days: int,
    due: DueInvoice,
) -> tuple[int, int]:
    """
    Returns (paid_inc, notified_inc).
    """
    if due.status == "paid" and due.paid_notified_at is None:
        notified = await _notify_paid_if_needed(
            session=session,
            bot=bot,
            invoice_id=due.invoice_id,
            master_id=due.master_id,
            master_telegram_id=due.master_telegram_id,
        )
        return 1, int(notified)

    use_case = CheckProPayment(session, express_pay_client=express_pay_client)
    result = await use_case.execute(
        CheckProPaymentRequest(
            master_telegram_id=due.master_telegram_id,
            invoice_id=due.invoice_id,
            pro_days=int(pro_days),
        ),
    )
    if not result.ok or result.invoice is None:
        return 0, 0

    if result.invoice.status.value != "paid":
        return 0, 0

    if result.invoice.paid_notified_at is not None:
        return 1, 0

    await bot.send_message(
        chat_id=due.master_telegram_id,
        text=billing_txt.pro_paid_message(paid_until=result.paid_until),
        parse_mode="HTML",
    )
    notified = await PaymentInvoiceRepository(session).mark_paid_notified(
        int(result.invoice.id),
        at=datetime.now(UTC),
    )
    return 1, int(notified)


async def run_loop(
    *,
    bot: Bot,
    express_pay_client: ExpressPayClient,
    redis: Redis,
    config: PaymentsLoopConfig,
) -> None:
    last_heartbeat_log_at: datetime | None = None
    while True:
        now_utc = datetime.now(UTC)
        obs = get_settings().observability
        await write_worker_heartbeat(
            redis,
            worker="payments",
            ttl=timedelta(seconds=int(obs.workers_heartbeat_ttl_sec)),
            now_utc=now_utc,
            ev=ev,
        )
        if (
            last_heartbeat_log_at is None
            or (now_utc - last_heartbeat_log_at).total_seconds() >= float(obs.workers_heartbeat_log_every_sec)
        ):
            ev.info(
                "workers.payments.heartbeat",
                ttl_sec=int(obs.workers_heartbeat_ttl_sec),
                log_every_sec=int(obs.workers_heartbeat_log_every_sec),
            )
            last_heartbeat_log_at = now_utc

        checked = 0
        paid = 0
        notified = 0

        due = await _load_due_invoices(now_utc=now_utc, batch_size=config.batch_size, min_recheck=config.min_recheck)
        for item in due:
            checked += 1
            try:
                async with active_session() as session:
                    paid_inc, notified_inc = await _process_due_invoice(
                        session=session,
                        bot=bot,
                        express_pay_client=express_pay_client,
                        pro_days=int(config.pro_days),
                        due=item,
                    )
                    paid += paid_inc
                    notified += notified_inc
            except Exception as exc:
                await ev.aexception(
                    "payments.check_failed",
                    exc=exc,
                    invoice_id=item.invoice_id,
                )

        ev.info(
            "payments.tick",
            checked=checked,
            paid=paid,
            notified=notified,
            candidates=len(due),
            tick_sec=int(config.tick.total_seconds()),
            batch_size=int(config.batch_size),
            min_recheck_sec=int(config.min_recheck.total_seconds()),
        )
        await asyncio.sleep(float(config.tick.total_seconds()))


async def main() -> None:
    args = _parse_args()
    settings = AppSettings.load(env_file=args.env_file)
    app_settings.set(settings)

    setup_logging(
        debug=bool(settings.debug),
        service="retention_bot",
        env=os.getenv("APP_ENV") or ("dev" if settings.debug else "prod"),
        version="0.1.0",
    )
    start_metrics_server()

    if settings.express_pay is None:
        raise RuntimeError("ExpressPay is not configured.")
    if settings.billing.pro_days is None:
        raise RuntimeError("BILLING__PRO_DAYS is not configured.")

    redis = Redis.from_url(settings.database.redis_url)
    bot = Bot(
        token=settings.telegram.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode="HTML"),
    )

    try:
        async with (
            Database.lifespan(url=settings.database.postgres_url),
            ExpressPayClient(settings.express_pay) as express_pay_client,
        ):
            await run_loop(
                bot=bot,
                express_pay_client=express_pay_client,
                redis=redis,
                config=PaymentsLoopConfig(
                    pro_days=int(settings.billing.pro_days),
                    tick=timedelta(seconds=int(args.tick_sec)),
                    batch_size=int(args.batch_size),
                    min_recheck=timedelta(seconds=int(args.min_recheck_sec)),
                ),
            )
    finally:
        await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        ev.info("payments.shutdown")
