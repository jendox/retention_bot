from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch


class DeletePersonalDataUseCaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_master_personal_data_anonymizes_audit_logs(self) -> None:
        from src.use_cases.delete_personal_data import DeleteMasterPersonalData

        session = object()
        with (
            patch("src.use_cases.delete_personal_data.MasterRepository") as MasterRepository,
            patch("src.use_cases.delete_personal_data.ClientRepository") as ClientRepository,
            patch("src.use_cases.delete_personal_data.ConsentRepository") as ConsentRepository,
            patch("src.use_cases.delete_personal_data.AuditLogRepository") as AuditLogRepository,
        ):
            masters = MasterRepository.return_value
            clients = ClientRepository.return_value
            consents = ConsentRepository.return_value
            audit = AuditLogRepository.return_value

            masters.delete_by_telegram_id = AsyncMock(return_value=True)
            consents.delete_consent = AsyncMock(return_value=True)
            clients.delete_orphan_offline_clients = AsyncMock(return_value=0)
            audit.anonymize_by_actor_id = AsyncMock(return_value=5)

            from src.use_cases import delete_personal_data as mod

            clients.get_by_telegram_id = AsyncMock(side_effect=mod.ClientNotFound("no client"))

            result = await DeleteMasterPersonalData(session).execute(telegram_id=1001)  # type: ignore[arg-type]

        self.assertTrue(result.deleted)
        self.assertFalse(result.other_role_exists)
        self.assertEqual(5, result.audit_logs_anonymized)
        audit.anonymize_by_actor_id.assert_awaited_once_with(actor_id=1001)

    async def test_delete_client_personal_data_anonymizes_audit_logs(self) -> None:
        from src.use_cases.delete_personal_data import DeleteClientPersonalData

        session = object()
        with (
            patch("src.use_cases.delete_personal_data.MasterRepository") as MasterRepository,
            patch("src.use_cases.delete_personal_data.ClientRepository") as ClientRepository,
            patch("src.use_cases.delete_personal_data.ConsentRepository") as ConsentRepository,
            patch("src.use_cases.delete_personal_data.AuditLogRepository") as AuditLogRepository,
        ):
            masters = MasterRepository.return_value
            clients = ClientRepository.return_value
            consents = ConsentRepository.return_value
            audit = AuditLogRepository.return_value

            clients.delete_by_telegram_id = AsyncMock(return_value=True)
            consents.delete_consent = AsyncMock(return_value=True)
            audit.anonymize_by_actor_id = AsyncMock(return_value=2)

            from src.use_cases import delete_personal_data as mod

            masters.get_by_telegram_id = AsyncMock(side_effect=mod.MasterNotFound("no master"))

            result = await DeleteClientPersonalData(session).execute(telegram_id=2001)  # type: ignore[arg-type]

        self.assertTrue(result.deleted)
        self.assertFalse(result.other_role_exists)
        self.assertEqual(2, result.audit_logs_anonymized)
        audit.anonymize_by_actor_id.assert_awaited_once_with(actor_id=2001)
