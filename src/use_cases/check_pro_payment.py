from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from src.integrations.expresspay import ExpressPayClient, InvoiceStatus
from src.integrations.expresspay.exceptions import ExpressPayError
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
    ) -> tuple[PaymentInvoiceUpdate, bool, datetime | None]:
        if provider_status in {InvoiceStatus.PAID, InvoiceStatus.PAID_BY_CARD}:
            paid_until = now_utc + timedelta(days=int(request.pro_days))
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

    async def execute(self, request: CheckProPaymentRequest) -> CheckProPaymentResult:
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

        try:
            provider_status = await self._express_pay.get_invoice_status(int(invoice.invoice_no))
        except ExpressPayError as exc:
            ev.warning(
                "billing.pro_payment_provider_error",
                master_id=master.id,
                invoice_id=invoice.id,
                invoice_no=invoice.invoice_no,
                error=str(exc),
            )
            return self._error(error=CheckProPaymentError.INVALID_REQUEST, error_detail="provider_error")
        now_utc = datetime.now(UTC)

        patch, granted_pro, paid_until = await self._apply_provider_status(
            master_id=int(master.id),
            provider_status=provider_status,
            now_utc=now_utc,
            request=request,
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

        return CheckProPaymentResult(
            ok=True,
            invoice=updated,
            provider_status=provider_status,
            granted_pro=granted_pro,
            paid_until=paid_until,
        )
