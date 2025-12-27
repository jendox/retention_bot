from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from src.integrations.expresspay import CurrencyCode
from src.schemas.payment_invoice import PaymentInvoiceStatus
from src.use_cases.create_pro_renewal_invoice import CreateProRenewalInvoice, CreateProRenewalInvoiceRequest


class BillingRenewalUseCaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_pro_renewal_invoice_creates_invoice(self) -> None:
        import src.use_cases.create_pro_renewal_invoice as uc

        class _MasterRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(id=7, telegram_id=telegram_id)

        class _InvoicesRepo:
            def __init__(self, session) -> None:
                self.create = mock.AsyncMock()

            async def get_latest_waiting_for_master(self, *, master_id: int):
                return None

        invoices_repo = _InvoicesRepo(session=object())
        invoices_repo.create.return_value = SimpleNamespace(
            id=10,
            master_id=7,
            invoice_no=555,
            invoice_url="https://pay.example/555",
            status=PaymentInvoiceStatus.WAITING,
            expires_at=datetime.now(UTC).replace(year=2099),
        )

        express = SimpleNamespace(create_invoice=mock.AsyncMock(return_value=(555, "https://pay.example/555")))

        with (
            mock.patch.object(uc, "MasterRepository", _MasterRepo),
            mock.patch.object(uc, "PaymentInvoiceRepository", lambda session: invoices_repo),
        ):
            result = await CreateProRenewalInvoice(session=object(), express_pay_client=express).execute(
                CreateProRenewalInvoiceRequest(
                    master_telegram_id=123,
                    amount=Decimal("15.00"),
                    currency=CurrencyCode.BYN,
                    reuse_waiting=False,
                ),
            )

        self.assertTrue(result.ok)
        self.assertIsNotNone(result.invoice)
        self.assertTrue(express.create_invoice.awaited)
