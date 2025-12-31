from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from src.integrations.expresspay import ExpressPayClient, InvoiceStatus
from src.integrations.expresspay.exceptions import ExpressPayApiError, ExpressPayError
from src.observability.audit_log import write_audit_log
from src.observability.events import EventLogger
from src.repositories import (
    MasterNotFound,
    MasterRepository,
    PaymentInvoiceNotFound,
    PaymentInvoiceRepository,
    SubscriptionRepository,
)
from src.schemas.payment_invoice import PaymentInvoice, PaymentInvoiceStatus, PaymentInvoiceUpdate

ev = EventLogger(__name__)

EXPRESSPAY_INVOICE_NOT_FOUND_MSG_CODE = 4041002


class CheckProPaymentError(StrEnum):
    MASTER_NOT_FOUND = "master_not_found"
    INVOICE_NOT_FOUND = "invoice_not_found"
    FORBIDDEN = "forbidden"
    INVALID_REQUEST = "invalid_request"


@dataclass(frozen=True)
class CheckProPaymentRequest:
    master_telegram_id: int
    invoice_id: int
    pro_days: int


@dataclass(frozen=True)
class CheckProPaymentResult:
    ok: bool

    invoice: PaymentInvoice | None = None
    provider_status: InvoiceStatus | None = None
    granted_pro: bool = False
    paid_until: datetime | None = None

    error: CheckProPaymentError | None = None
    error_detail: str | None = None


class CheckProPayment:
    def __init__(self, session: AsyncSession, *, express_pay_client: ExpressPayClient) -> None:
        self._session = session
        self._express_pay = express_pay_client
        self._masters = MasterRepository(session)
        self._invoices = PaymentInvoiceRepository(session)
        self._subs = SubscriptionRepository(session)

    @staticmethod
    def _error(*, error: CheckProPaymentError, error_detail: str | None = None) -> CheckProPaymentResult:
        return CheckProPaymentResult(ok=False, error=error, error_detail=error_detail)

    @staticmethod
    def _validate(request: CheckProPaymentRequest) -> CheckProPaymentResult | None:
        if request.master_telegram_id <= 0 or request.invoice_id <= 0:
            return CheckProPayment._error(error=CheckProPaymentError.INVALID_REQUEST)
        if request.pro_days <= 0:
            return CheckProPayment._error(
                error=CheckProPaymentError.INVALID_REQUEST,
                error_detail="pro_days must be > 0",
            )
        return None

    async def _apply_provider_status(
        self,
        *,
        master_id: int,
        provider_status: InvoiceStatus,
        now_utc: datetime,
        request: CheckProPaymentRequest,
        subscription,
    ) -> tuple[PaymentInvoiceUpdate, bool, datetime | None]:
        if provider_status in {InvoiceStatus.PAID, InvoiceStatus.PAID_BY_CARD}:
            base_until = now_utc
            if subscription is not None:
                paid_until_current = getattr(subscription, "paid_until", None)
                trial_until_current = getattr(subscription, "trial_until", None)
                if paid_until_current is not None and paid_until_current > base_until:
                    base_until = paid_until_current
                if trial_until_current is not None and trial_until_current > base_until:
                    base_until = trial_until_current

            paid_until = base_until + timedelta(days=int(request.pro_days))
            await self._subs.grant_pro(master_id, paid_until=paid_until)
            return (
                PaymentInvoiceUpdate(
                    status=PaymentInvoiceStatus.PAID,
                    provider_status_code=int(provider_status),
                    paid_at=now_utc,
                    last_checked_at=now_utc,
                ),
                True,
                paid_until,
            )

        if provider_status == InvoiceStatus.EXPIRED:
            return (
                PaymentInvoiceUpdate(
                    status=PaymentInvoiceStatus.EXPIRED,
                    provider_status_code=int(provider_status),
                    last_checked_at=now_utc,
                ),
                False,
                None,
            )

        if provider_status == InvoiceStatus.CANCELED:
            return (
                PaymentInvoiceUpdate(
                    status=PaymentInvoiceStatus.CANCELED,
                    provider_status_code=int(provider_status),
                    last_checked_at=now_utc,
                ),
                False,
                None,
            )

        return (
            PaymentInvoiceUpdate(
                provider_status_code=int(provider_status),
                last_checked_at=now_utc,
            ),
            False,
            None,
        )

    async def execute(self, request: CheckProPaymentRequest) -> CheckProPaymentResult:  # noqa: C901, PLR0911
        invalid = self._validate(request)
        if invalid is not None:
            return invalid

        try:
            master = await self._masters.get_by_telegram_id(request.master_telegram_id)
        except MasterNotFound:
            return self._error(error=CheckProPaymentError.MASTER_NOT_FOUND)

        try:
            invoice = await self._invoices.get_by_id(request.invoice_id)
        except PaymentInvoiceNotFound:
            return self._error(error=CheckProPaymentError.INVOICE_NOT_FOUND)

        if int(invoice.master_id) != int(master.id):
            return self._error(error=CheckProPaymentError.FORBIDDEN)

        if invoice.status == PaymentInvoiceStatus.PAID:
            now_utc = datetime.now(UTC)
            await self._invoices.touch_last_checked_at(request.invoice_id, at=now_utc)
            return CheckProPaymentResult(
                ok=True,
                invoice=invoice,
                provider_status=None,
                granted_pro=False,
                paid_until=None,
            )

        try:
            provider_status = await self._express_pay.get_invoice_status(int(invoice.invoice_no))
        except ExpressPayError as exc:
            now_utc = datetime.now(UTC)

            # Throttle repeated checks on provider errors.
            await self._invoices.touch_last_checked_at(request.invoice_id, at=now_utc)

            # ExpressPay: 4041002 = "Счет на оплату не найден"
            # This is a terminal state for our invoice_no: it cannot ever become paid.
            if (
                isinstance(exc, ExpressPayApiError)
                and int(exc.payload.msg_code) == EXPRESSPAY_INVOICE_NOT_FOUND_MSG_CODE
            ):
                await self._invoices.update_by_id(
                    request.invoice_id,
                    PaymentInvoiceUpdate(
                        status=PaymentInvoiceStatus.CANCELED,
                        last_checked_at=now_utc,
                    ),
                )
                updated = await self._invoices.get_by_id(request.invoice_id)
                ev.warning(
                    "billing.pro_payment_invoice_not_found",
                    master_id=master.id,
                    invoice_id=updated.id,
                    invoice_no=updated.invoice_no,
                    msg_code=int(exc.payload.msg_code),
                )
                return CheckProPaymentResult(
                    ok=True,
                    invoice=updated,
                    provider_status=None,
                    granted_pro=False,
                    paid_until=None,
                )

            ev.warning(
                "billing.pro_payment_provider_error",
                master_id=master.id,
                invoice_id=invoice.id,
                invoice_no=invoice.invoice_no,
                error=str(exc),
            )
            return self._error(error=CheckProPaymentError.INVALID_REQUEST, error_detail="provider_error")
        now_utc = datetime.now(UTC)
        subscription = await self._subs.get_by_master_id(int(master.id))

        # Avoid granting twice in concurrent checks: re-check under row lock *after* provider status is known.
        if provider_status in {InvoiceStatus.PAID, InvoiceStatus.PAID_BY_CARD}:
            locked = await self._invoices.get_by_id_for_update(request.invoice_id)
            if locked.status == PaymentInvoiceStatus.PAID:
                await self._invoices.touch_last_checked_at(request.invoice_id, at=now_utc)
                return CheckProPaymentResult(
                    ok=True,
                    invoice=locked,
                    provider_status=provider_status,
                    granted_pro=False,
                    paid_until=None,
                )

        patch, granted_pro, paid_until = await self._apply_provider_status(
            master_id=int(master.id),
            provider_status=provider_status,
            now_utc=now_utc,
            request=request,
            subscription=subscription,
        )

        await self._invoices.update_by_id(request.invoice_id, patch)
        updated = await self._invoices.get_by_id(request.invoice_id)

        ev.info(
            "billing.pro_payment_checked",
            master_id=master.id,
            invoice_id=updated.id,
            invoice_no=updated.invoice_no,
            provider_status=int(provider_status),
            status=str(updated.status.value),
            granted_pro=bool(granted_pro),
        )
        if granted_pro:
            ev.info(
                "payment_success",
                master_id=master.id,
                invoice_id=updated.id,
                invoice_no=updated.invoice_no,
                paid_until=paid_until,
            )
            write_audit_log(
                self._session,
                event="payment_success",
                actor="master",
                actor_id=int(request.master_telegram_id),
                master_id=int(master.id),
                invoice_id=int(updated.id),
                metadata={"invoice_no": int(updated.invoice_no), "paid_until": paid_until},
            )
            ev.info(
                "subscription_renewed",
                master_id=master.id,
                paid_until=paid_until,
            )
            write_audit_log(
                self._session,
                event="subscription_renewed",
                actor="system",
                master_id=int(master.id),
                metadata={"paid_until": paid_until},
            )

        return CheckProPaymentResult(
            ok=True,
            invoice=updated,
            provider_status=provider_status,
            granted_pro=granted_pro,
            paid_until=paid_until,
        )
