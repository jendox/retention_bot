from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from src.integrations.expresspay import CurrencyCode, InvoiceStatus
from src.schemas.payment_invoice import PaymentInvoiceStatus
from src.use_cases.check_pro_payment import CheckProPayment, CheckProPaymentRequest
from src.use_cases.create_pro_invoice import CreateProInvoice, CreateProInvoiceError, CreateProInvoiceRequest


class BillingUseCaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_pro_invoice_calls_expresspay_with_lifetime_only(self) -> None:
        import src.use_cases.create_pro_invoice as uc

        class _MasterRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(id=1, telegram_id=telegram_id)

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

            async def get_plan(self, *, master_id: int, now=None):
                return SimpleNamespace(is_pro=False)

        class _InvoicesRepo:
            def __init__(self, session) -> None:
                self.create = mock.AsyncMock()

            async def get_latest_waiting_for_master(self, *, master_id: int):
                return None

        express = SimpleNamespace(create_invoice=mock.AsyncMock(return_value=(555, "https://pay.example/555")))

        invoices_repo = _InvoicesRepo(session=object())
        invoices_repo.create.return_value = SimpleNamespace(
            id=10,
            master_id=1,
            invoice_no=555,
            invoice_url="https://pay.example/555",
            status=PaymentInvoiceStatus.WAITING,
            expires_at=datetime.now(UTC).replace(year=2099),
        )

        with (
            mock.patch.object(uc, "MasterRepository", _MasterRepo),
            mock.patch.object(uc, "EntitlementsService", _Entitlements),
            mock.patch.object(uc, "PaymentInvoiceRepository", lambda session: invoices_repo),
        ):
            result = await CreateProInvoice(session=object(), express_pay_client=express).execute(
                CreateProInvoiceRequest(master_telegram_id=1, amount=Decimal("10.00"), currency=CurrencyCode.BYN),
            )

        self.assertTrue(result.ok)
        self.assertIsNotNone(result.invoice)
        self.assertTrue(express.create_invoice.awaited)
        sent = express.create_invoice.await_args.args[0]
        self.assertIsNone(sent.expires_at)
        self.assertEqual(sent.lifetime_seconds, 60 * 30)
        self.assertEqual(sent.master_id, 1)

    async def test_create_pro_invoice_reuses_waiting(self) -> None:
        import src.use_cases.create_pro_invoice as uc

        class _MasterRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(id=1, telegram_id=telegram_id)

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

            async def get_plan(self, *, master_id: int, now=None):
                return SimpleNamespace(is_pro=False)

        class _InvoicesRepo:
            def __init__(self, session) -> None:
                pass

            async def get_latest_waiting_for_master(self, *, master_id: int):
                return SimpleNamespace(
                    id=10,
                    master_id=master_id,
                    invoice_no=123,
                    invoice_url="https://pay.example/123",
                    status=PaymentInvoiceStatus.WAITING,
                    expires_at=datetime.now(UTC).replace(year=2099),
                )

        express = SimpleNamespace(create_invoice=mock.AsyncMock())

        with (
            mock.patch.object(uc, "MasterRepository", _MasterRepo),
            mock.patch.object(uc, "EntitlementsService", _Entitlements),
            mock.patch.object(uc, "PaymentInvoiceRepository", _InvoicesRepo),
        ):
            result = await CreateProInvoice(session=object(), express_pay_client=express).execute(
                CreateProInvoiceRequest(master_telegram_id=1, amount=Decimal("10.00"), currency=CurrencyCode.BYN),
            )

        self.assertTrue(result.ok)
        self.assertTrue(result.reused)
        express.create_invoice.assert_not_awaited()

    async def test_create_pro_invoice_returns_already_pro(self) -> None:
        import src.use_cases.create_pro_invoice as uc

        class _MasterRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(id=1, telegram_id=telegram_id)

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

            async def get_plan(self, *, master_id: int, now=None):
                return SimpleNamespace(is_pro=True)

        class _InvoicesRepo:
            def __init__(self, session) -> None:
                pass

        express = SimpleNamespace(create_invoice=mock.AsyncMock())

        with (
            mock.patch.object(uc, "MasterRepository", _MasterRepo),
            mock.patch.object(uc, "EntitlementsService", _Entitlements),
            mock.patch.object(uc, "PaymentInvoiceRepository", _InvoicesRepo),
        ):
            result = await CreateProInvoice(session=object(), express_pay_client=express).execute(
                CreateProInvoiceRequest(master_telegram_id=1, amount=Decimal("10.00"), currency=CurrencyCode.BYN),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, CreateProInvoiceError.ALREADY_PRO)

    async def test_check_pro_payment_grants_pro_on_paid(self) -> None:
        import src.use_cases.check_pro_payment as uc

        class _MasterRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(id=1)

        class _InvoicesRepo:
            def __init__(self, session) -> None:
                self._saved = SimpleNamespace(
                    id=7,
                    master_id=1,
                    invoice_no=123,
                    status=PaymentInvoiceStatus.WAITING,
                )

            async def get_by_id(self, invoice_id: int):
                return self._saved

            async def get_by_id_for_update(self, invoice_id: int):
                return self._saved

            async def update_by_id(self, invoice_id: int, patch):
                if getattr(patch, "status", None) is not None:
                    self._saved.status = patch.status
                return True

        class _SubsRepo:
            def __init__(self, session) -> None:
                self.grant_pro = mock.AsyncMock()

            async def get_by_master_id(self, master_id: int):
                return None

        express = SimpleNamespace(get_invoice_status=mock.AsyncMock(return_value=InvoiceStatus.PAID))

        with (
            mock.patch.object(uc, "MasterRepository", _MasterRepo),
            mock.patch.object(uc, "PaymentInvoiceRepository", _InvoicesRepo),
            mock.patch.object(uc, "SubscriptionRepository", _SubsRepo),
        ):
            result = await CheckProPayment(session=object(), express_pay_client=express).execute(
                CheckProPaymentRequest(master_telegram_id=1, invoice_id=7, pro_days=30),
            )

        self.assertTrue(result.ok)
        self.assertTrue(result.granted_pro)
        self.assertIsNotNone(result.paid_until)

    async def test_check_pro_payment_extends_from_existing_paid_until(self) -> None:
        import src.use_cases.check_pro_payment as uc

        fixed_now = datetime(2026, 1, 1, tzinfo=UTC)

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now

        class _MasterRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(id=1)

        class _InvoicesRepo:
            def __init__(self, session) -> None:
                self._saved = SimpleNamespace(
                    id=7,
                    master_id=1,
                    invoice_no=123,
                    status=PaymentInvoiceStatus.WAITING,
                )

            async def get_by_id(self, invoice_id: int):
                return self._saved

            async def get_by_id_for_update(self, invoice_id: int):
                return self._saved

            async def update_by_id(self, invoice_id: int, patch):
                return True

        class _SubsRepo:
            def __init__(self, session) -> None:
                self.grant_pro = mock.AsyncMock()

            async def get_by_master_id(self, master_id: int):
                return SimpleNamespace(paid_until=fixed_now + timedelta(days=10), trial_until=None)

        express = SimpleNamespace(get_invoice_status=mock.AsyncMock(return_value=InvoiceStatus.PAID))

        with (
            mock.patch.object(uc, "datetime", _FixedDatetime),
            mock.patch.object(uc, "MasterRepository", _MasterRepo),
            mock.patch.object(uc, "PaymentInvoiceRepository", _InvoicesRepo),
            mock.patch.object(uc, "SubscriptionRepository", _SubsRepo),
        ):
            result = await CheckProPayment(session=object(), express_pay_client=express).execute(
                CheckProPaymentRequest(master_telegram_id=1, invoice_id=7, pro_days=30),
            )

        self.assertTrue(result.ok)
        self.assertTrue(result.granted_pro)
        self.assertEqual(result.paid_until, fixed_now + timedelta(days=40))

    async def test_check_pro_payment_is_idempotent_for_paid_invoice(self) -> None:
        import src.use_cases.check_pro_payment as uc

        class _MasterRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(id=1)

        class _InvoicesRepo:
            def __init__(self, session) -> None:
                self._saved = SimpleNamespace(
                    id=7,
                    master_id=1,
                    invoice_no=123,
                    status=PaymentInvoiceStatus.PAID,
                )

            async def get_by_id(self, invoice_id: int):
                return self._saved

            async def touch_last_checked_at(self, invoice_id: int, *, at):
                return True

        class _SubsRepo:
            def __init__(self, session) -> None:
                self.grant_pro = mock.AsyncMock()

            async def get_by_master_id(self, master_id: int):
                return None

        express = SimpleNamespace(get_invoice_status=mock.AsyncMock(return_value=InvoiceStatus.PAID))

        with (
            mock.patch.object(uc, "MasterRepository", _MasterRepo),
            mock.patch.object(uc, "PaymentInvoiceRepository", _InvoicesRepo),
            mock.patch.object(uc, "SubscriptionRepository", _SubsRepo),
        ):
            result = await CheckProPayment(session=object(), express_pay_client=express).execute(
                CheckProPaymentRequest(master_telegram_id=1, invoice_id=7, pro_days=30),
            )

        self.assertTrue(result.ok)
        self.assertFalse(result.granted_pro)
        express.get_invoice_status.assert_not_awaited()
