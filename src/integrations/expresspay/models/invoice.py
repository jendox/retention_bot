from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import IntEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

AccountSeed = Annotated[int, Field(ge=1)]


class CurrencyCode(IntEnum):
    BYN = 933
    EUR = 978
    USD = 840
    RUB = 643


class InvoiceStatus(IntEnum):
    WAITING = 1
    EXPIRED = 2
    PAID = 3
    PARTIALLY_PAID = 4
    CANCELED = 5
    PAID_BY_CARD = 6
    PAYMENT_REFUNDED = 7


class CreateInvoiceInput(BaseModel):
    master_id: AccountSeed
    amount: Decimal = Field(gt=0)
    currency: CurrencyCode
    description: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    # взаимоисключающие варианты:
    expires_at: datetime | date | None = None
    lifetime_seconds: int | None = Field(default=None, ge=1, le=31_536_000)  # до 1 года

    @model_validator(mode="after")
    def _validate_ttl(self):
        if self.expires_at is not None and self.lifetime_seconds is not None:
            raise ValueError("Use either expires_at or lifetime_seconds, not both")
        return self


class UpdateInvoicePatch(BaseModel):
    """
    Данные для изменения счета.
    Можно расширять (ФИО/адрес и т.д.), но для бота обычно хватает суммы/описания/срока.
    """
    model_config = ConfigDict(extra="forbid")

    amount: Decimal | None = Field(default=None, gt=0)
    currency: CurrencyCode | None = None
    description: Annotated[str, StringConstraints(min_length=1, max_length=1024)] | None = None
    expires_at: datetime | date | None = None

    @model_validator(mode="after")
    def _validate_any(self):
        if not any([self.amount, self.currency, self.description, self.expires_at]):
            raise ValueError("At least one field must be provided for update")
        return self


class CreateInvoiceResponse(BaseModel):
    InvoiceNo: int
    InvoiceUrl: str | None = None


class InvoiceStatusResponse(BaseModel):
    Status: InvoiceStatus
