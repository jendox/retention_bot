from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock


class AuditLogRepositoryAnonymizeTests(unittest.IsolatedAsyncioTestCase):
    async def test_anonymize_by_actor_id_updates_rows(self) -> None:
        from sqlalchemy.sql.dml import Update

        from src.models.audit_log import AuditLog
        from src.repositories.audit_log import AuditLogRepository

        class _Session:
            def __init__(self) -> None:
                self.execute = AsyncMock(return_value=SimpleNamespace(rowcount=3))
                self.flush = AsyncMock()

        session = _Session()
        repo = AuditLogRepository(session)  # type: ignore[arg-type]

        count = await repo.anonymize_by_actor_id(actor_id=123)

        self.assertEqual(3, count)
        session.execute.assert_awaited_once()
        session.flush.assert_awaited_once()

        stmt = session.execute.call_args.args[0]
        self.assertIsInstance(stmt, Update)

        self.assertEqual("audit_logs", stmt.table.name)
        self.assertIn(AuditLog.__table__.c.actor_id, stmt._values)  # noqa: SLF001
        self.assertIn(AuditLog.__table__.c.actor, stmt._values)  # noqa: SLF001
        self.assertIn(AuditLog.__table__.c.trace_id, stmt._values)  # noqa: SLF001
        self.assertIn(AuditLog.__table__.c.metadata, stmt._values)  # noqa: SLF001
