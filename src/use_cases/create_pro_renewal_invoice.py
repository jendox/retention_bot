from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from src.integrations.expresspay import CreateInvoiceInput, CurrencyCode, ExpressPayClient
from src.integrations.expresspay.exceptions import ExpressPayError
from src.observability.events import EventLogger
from src.repositories import MasterNotFound, MasterRepository, PaymentInvoiceRepository
from src.schemas.payment_invoice import PaymentInvoice, PaymentInvoiceCreate, PaymentInvoiceStatus, PaymentProvider

ev = EventLogger(__name__)


class CreateProRenewalInvoiceError(StrEnum):
    MASTER_NOT_FOUND = "master_not_found"
    INVALID_REQUEST = "invalid_request"
    PROVIDER_ERROR = "provider_error"


@dataclass(frozen=True)
class CreateProRenewalInvoiceRequest:
    master_telegram_id: int
    amount: Decimal
    currency: CurrencyCode = CurrencyCode.BYN
    description: str = "Pro subscription renewal"
    lifetime_seconds: int | None = 60 * 30  # 30 min
    expires_at: datetime | None = None
    reuse_waiting: bool = True


@dataclass(frozen=True)
class CreateProRenewalInvoiceResult:
    ok: bool

    invoice: PaymentInvoice | None = None
    invoice_url: str | None = None

    reused: bool = False

    error: CreateProRenewalInvoiceError | None = None
    error_detail: str | None = None


class CreateProRenewalInvoice:
    def __init__(self, session: AsyncSession, *, express_pay_client: ExpressPayClient) -> None:
        self._session = session
        self._express_pay = express_pay_client
        self._masters = MasterRepository(session)
        self._invoices = PaymentInvoiceRepository(session)

    @staticmethod
    def _error(
        *,
        error: CreateProRenewalInvoiceError,
        error_detail: str | None = None,
    ) -> CreateProRenewalInvoiceResult:
        return CreateProRenewalInvoiceResult(ok=False, error=error, error_detail=error_detail)

    @staticmethod
    def _validate(request: CreateProRenewalInvoiceRequest) -> CreateProRenewalInvoiceResult | None:
        if request.master_telegram_id <= 0:
            return CreateProRenewalInvoice._error(
                error=CreateProRenewalInvoiceError.INVALID_REQUEST,
                error_detail="master_telegram_id must be > 0",
            )
        if request.amount <= 0:
            return CreateProRenewalInvoice._error(
                error=CreateProRenewalInvoiceError.INVALID_REQUEST,
                error_detail="amount must be > 0",
            )
        return None

    @staticmethod
    def _compute_expires_at(request: CreateProRenewalInvoiceRequest, *, now_utc: datetime) -> datetime | None:
        if request.expires_at is not None:
            return request.expires_at.astimezone(UTC) if request.expires_at.tzinfo else request.expires_at
        if request.lifetime_seconds is not None:
            return now_utc + timedelta(seconds=int(request.lifetime_seconds))
        return None

    async def _maybe_reuse_waiting(self, *, master_id: int, now_utc: datetime) -> PaymentInvoice | None:
        existing = await self._invoices.get_latest_waiting_for_master(master_id=master_id)
        if existing is None:
            return None
        if existing.expires_at is not None and existing.expires_at <= now_utc:
            return None
        return existing

    async def execute(self, request: CreateProRenewalInvoiceRequest) -> CreateProRenewalInvoiceResult:
        invalid = self._validate(request)
        if invalid is not None:
            return invalid

        try:
            master = await self._masters.get_by_telegram_id(request.master_telegram_id)
        except MasterNotFound:
            return self._error(error=CreateProRenewalInvoiceError.MASTER_NOT_FOUND)

        now_utc = datetime.now(UTC)
        if request.reuse_waiting:
            existing = await self._maybe_reuse_waiting(master_id=int(master.id), now_utc=now_utc)
            if existing is not None:
                ev.info(
                    "billing.pro_renewal_invoice_reused",
                    master_id=master.id,
                    invoice_id=existing.id,
                    invoice_no=existing.invoice_no,
                )
                return CreateProRenewalInvoiceResult(
                    ok=True,
                    invoice=existing,
                    invoice_url=existing.invoice_url,
                    reused=True,
                )

        expires_at = self._compute_expires_at(request, now_utc=now_utc)
        api_expires_at = expires_at if request.expires_at is not None else None
        api_lifetime_seconds = request.lifetime_seconds if request.expires_at is None else None

        try:
            invoice_no, invoice_url = await self._express_pay.create_invoice(
                CreateInvoiceInput(
                    master_id=int(master.id),
                    amount=request.amount,
                    currency=request.currency,
                    description=request.description,
                    expires_at=api_expires_at,
                    lifetime_seconds=api_lifetime_seconds,
                ),
            )
        except ExpressPayError as exc:
            ev.warning("billing.pro_renewal_invoice_provider_error", master_id=master.id, error=str(exc))
            return self._error(error=CreateProRenewalInvoiceError.PROVIDER_ERROR, error_detail="provider_error")

        created = await self._invoices.create(
            PaymentInvoiceCreate(
                master_id=int(master.id),
                provider=PaymentProvider.EXPRESSPAY,
                invoice_no=int(invoice_no),
                invoice_url=invoice_url,
                amount=float(request.amount),
                currency=int(request.currency),
                status=PaymentInvoiceStatus.WAITING,
                expires_at=expires_at,
            ),
        )

        ev.info(
            "billing.pro_renewal_invoice_created",
            master_id=master.id,
            invoice_id=created.id,
            invoice_no=created.invoice_no,
        )
        return CreateProRenewalInvoiceResult(ok=True, invoice=created, invoice_url=invoice_url, reused=False)
