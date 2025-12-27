from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, select, update

from src.models import PaymentInvoice as PaymentInvoiceEntity
from src.repositories.base import BaseRepository
from src.schemas.payment_invoice import PaymentInvoice, PaymentInvoiceCreate, PaymentInvoiceUpdate


class PaymentInvoiceNotFound(Exception): ...


class PaymentInvoiceRepository(BaseRepository):
    async def create(self, invoice: PaymentInvoiceCreate) -> PaymentInvoice:
        entity = invoice.to_db_entity()
        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)
        return PaymentInvoice.from_db_entity(entity)

    async def get_by_id(self, invoice_id: int) -> PaymentInvoice:
        stmt = select(PaymentInvoiceEntity).where(PaymentInvoiceEntity.id == int(invoice_id))
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise PaymentInvoiceNotFound("Payment invoice not found.")
        return PaymentInvoice.from_db_entity(entity)

    async def get_by_id_for_update(self, invoice_id: int) -> PaymentInvoice:
        stmt = select(PaymentInvoiceEntity).where(PaymentInvoiceEntity.id == int(invoice_id)).with_for_update()
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise PaymentInvoiceNotFound("Payment invoice not found.")
        return PaymentInvoice.from_db_entity(entity)

    async def get_latest_for_master(self, *, master_id: int) -> PaymentInvoice | None:
        stmt = (
            select(PaymentInvoiceEntity)
            .where(PaymentInvoiceEntity.master_id == int(master_id))
            .order_by(PaymentInvoiceEntity.id.desc())
            .limit(1)
        )
        entity = await self._session.scalar(stmt)
        return PaymentInvoice.from_db_entity(entity) if entity is not None else None

    async def get_latest_waiting_for_master(self, *, master_id: int) -> PaymentInvoice | None:
        stmt = (
            select(PaymentInvoiceEntity)
            .where(PaymentInvoiceEntity.master_id == int(master_id))
            .where(PaymentInvoiceEntity.status == "waiting")
            .order_by(PaymentInvoiceEntity.id.desc())
            .limit(1)
        )
        entity = await self._session.scalar(stmt)
        return PaymentInvoice.from_db_entity(entity) if entity is not None else None

    async def update_by_id(self, invoice_id: int, patch: PaymentInvoiceUpdate) -> bool:
        values = patch.model_dump(exclude_unset=True)
        if not values:
            return True
        stmt = update(PaymentInvoiceEntity).where(PaymentInvoiceEntity.id == int(invoice_id)).values(**values)
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0

    async def touch_last_checked_at(self, invoice_id: int, *, at: datetime) -> bool:
        stmt = (
            update(PaymentInvoiceEntity)
            .where(PaymentInvoiceEntity.id == int(invoice_id))
            .values(last_checked_at=at)
        )
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0

    async def mark_paid_notified(self, invoice_id: int, *, at: datetime) -> bool:
        stmt = (
            update(PaymentInvoiceEntity)
            .where(
                and_(
                    PaymentInvoiceEntity.id == int(invoice_id),
                    PaymentInvoiceEntity.paid_notified_at.is_(None),
                ),
            )
            .values(paid_notified_at=at)
        )
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0
