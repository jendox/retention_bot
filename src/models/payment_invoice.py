from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.sa import Base


class PaymentProvider(StrEnum):
    EXPRESSPAY = "expresspay"


class PaymentInvoiceStatus(StrEnum):
    WAITING = "waiting"
    PAID = "paid"
    EXPIRED = "expired"
    CANCELED = "canceled"
    FAILED = "failed"


class PaymentInvoice(Base):
    __tablename__ = "payment_invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    master_id: Mapped[int] = mapped_column(
        ForeignKey("masters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default=PaymentProvider.EXPRESSPAY)

    invoice_no: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    invoice_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    amount: Mapped[float] = mapped_column(Numeric(19, 2), nullable=False)
    currency: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=PaymentInvoiceStatus.WAITING)
    provider_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
