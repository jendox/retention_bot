from __future__ import annotations

import argparse
import asyncio
import os
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
from src.repositories.payment_invoice import PaymentInvoiceRepository
from src.settings import AppSettings, app_settings
from src.texts import billing as billing_txt
from src.use_cases.check_pro_payment import CheckProPayment, CheckProPaymentRequest

ev = EventLogger("workers.payments")


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
) -> list[tuple[int, int]]:
    """
    Returns list of (invoice_id, master_telegram_id).
    """
    cutoff = now_utc - min_recheck
    async with session_local() as session:
        stmt = (
            select(PaymentInvoiceEntity.id, MasterEntity.telegram_id)
            .join(MasterEntity, MasterEntity.id == PaymentInvoiceEntity.master_id)
            .where(PaymentInvoiceEntity.status == "waiting")
            .where(or_(PaymentInvoiceEntity.expires_at.is_(None), PaymentInvoiceEntity.expires_at > now_utc))
            .where(or_(PaymentInvoiceEntity.last_checked_at.is_(None), PaymentInvoiceEntity.last_checked_at < cutoff))
            .order_by(PaymentInvoiceEntity.last_checked_at.asc().nullsfirst(), PaymentInvoiceEntity.id.asc())
            .limit(int(batch_size))
        )
        rows = (await session.execute(stmt)).all()
        return [(int(invoice_id), int(master_telegram_id)) for invoice_id, master_telegram_id in rows]


async def run_loop(
    *,
    bot: Bot,
    express_pay_client: ExpressPayClient,
    pro_days: int,
    tick: timedelta,
    batch_size: int,
    min_recheck: timedelta,
) -> None:
    while True:
        now_utc = datetime.now(UTC)
        checked = 0
        paid = 0
        notified = 0

        due = await _load_due_invoices(now_utc=now_utc, batch_size=batch_size, min_recheck=min_recheck)
        for invoice_id, master_telegram_id in due:
            checked += 1
            try:
                async with active_session() as session:
                    use_case = CheckProPayment(session, express_pay_client=express_pay_client)
                    result = await use_case.execute(
                        CheckProPaymentRequest(
                            master_telegram_id=master_telegram_id,
                            invoice_id=invoice_id,
                            pro_days=int(pro_days),
                        ),
                    )
                    if not result.ok or result.invoice is None:
                        continue

                    if result.invoice.status.value != "paid":
                        continue

                    paid += 1
                    if result.invoice.paid_notified_at is not None:
                        continue

                    await bot.send_message(
                        chat_id=master_telegram_id,
                        text=billing_txt.pro_paid_message(paid_until=result.paid_until),
                        parse_mode="HTML",
                    )
                    await PaymentInvoiceRepository(session).mark_paid_notified(
                        int(result.invoice.id),
                        at=datetime.now(UTC),
                    )
                    notified += 1
            except Exception as exc:
                await ev.aexception(
                    "payments.check_failed",
                    exc=exc,
                    invoice_id=invoice_id,
                )

        ev.info(
            "payments.tick",
            checked=checked,
            paid=paid,
            notified=notified,
            candidates=len(due),
            tick_sec=int(tick.total_seconds()),
            batch_size=int(batch_size),
            min_recheck_sec=int(min_recheck.total_seconds()),
        )
        await asyncio.sleep(float(tick.total_seconds()))


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
                pro_days=int(settings.billing.pro_days),
                tick=timedelta(seconds=int(args.tick_sec)),
                batch_size=int(args.batch_size),
                min_recheck=timedelta(seconds=int(args.min_recheck_sec)),
            )
    finally:
        await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        ev.info("payments.shutdown")
