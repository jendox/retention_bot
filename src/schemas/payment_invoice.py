from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class PaymentProvider(StrEnum):
    EXPRESSPAY = "expresspay"


class PaymentInvoiceStatus(StrEnum):
    WAITING = "waiting"
    PAID = "paid"
    EXPIRED = "expired"
    CANCELED = "canceled"
    FAILED = "failed"


class PaymentInvoice(BaseModel):
    id: int
    master_id: int

    provider: PaymentProvider
    invoice_no: int
    invoice_url: str | None = None

    amount: float
    currency: int

    status: PaymentInvoiceStatus
    provider_status_code: int | None = None

    expires_at: datetime | None = None
    paid_at: datetime | None = None
    paid_notified_at: datetime | None = None
    last_checked_at: datetime | None = None

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    @classmethod
    def from_db_entity(cls, entity) -> PaymentInvoice:
        return cls.model_validate(entity)


class PaymentInvoiceCreate(BaseModel):
    master_id: int = Field(ge=1)
    provider: PaymentProvider = PaymentProvider.EXPRESSPAY
    invoice_no: int = Field(ge=1)
    invoice_url: str | None = None
    amount: float = Field(gt=0)
    currency: int = Field(ge=1)
    status: PaymentInvoiceStatus = PaymentInvoiceStatus.WAITING
    provider_status_code: int | None = None
    expires_at: datetime | None = None

    model_config = ConfigDict(extra="forbid")

    def to_db_entity(self):
        from src.models.payment_invoice import PaymentInvoice as PaymentInvoiceEntity

        return PaymentInvoiceEntity(**self.model_dump())


class PaymentInvoiceUpdate(BaseModel):
    status: PaymentInvoiceStatus | None = None
    provider_status_code: int | None = None
    expires_at: datetime | None = None
    paid_at: datetime | None = None
    paid_notified_at: datetime | None = None
    last_checked_at: datetime | None = None

    model_config = ConfigDict(extra="forbid")
