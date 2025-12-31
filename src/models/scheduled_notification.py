from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.sa import Base


class ScheduledNotification(Base):
    __tablename__ = "scheduled_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # e.g. NotificationEvent.REMINDER_24H.value
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    # e.g. RecipientKind.CLIENT.value / RecipientKind.MASTER.value
    recipient: Mapped[str] = mapped_column(String(16), nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    master_id: Mapped[int | None] = mapped_column(ForeignKey("masters.id", ondelete="CASCADE"), nullable=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=True)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), nullable=True)
    invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_invoices.id", ondelete="CASCADE"),
        nullable=True,
    )

    # used to invalidate reminders on reschedule (start_at change)
    booking_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # "pending" | "sending" | "sent" | "cancelled" | "failed"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # unique key for idempotent scheduling
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)

    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_scheduled_notifications_status_due", "status", "due_at"),
        Index("ix_scheduled_notifications_booking_event", "booking_id", "event"),
        Index("ix_scheduled_notifications_invoice_event", "invoice_id", "event"),
    )
