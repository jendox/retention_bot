from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.sa import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    event: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    actor: Mapped[str | None] = mapped_column(String(16), nullable=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    master_id: Mapped[int | None] = mapped_column(ForeignKey("masters.id", ondelete="SET NULL"), nullable=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"), nullable=True)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True)
    invite_id: Mapped[int | None] = mapped_column(ForeignKey("invites.id", ondelete="SET NULL"), nullable=True)
    invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_invoices.id", ondelete="SET NULL"),
        nullable=True,
    )

    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    meta: Mapped[dict[str, object] | None] = mapped_column("metadata", JSONB, nullable=True)
