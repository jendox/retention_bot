from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class InviteRepositoryCreateTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_retries_without_session_rollback(self) -> None:
        from src.repositories.invite import InviteRepository
        from src.schemas.enums import InviteType
        from src.schemas.invite import Invite

        class _Result:
            def __init__(self, row):
                self._row = row

            def first(self):
                return self._row

        class _Session:
            def __init__(self):
                self.execute = AsyncMock(
                    side_effect=[
                        _Result(None),
                        _Result(SimpleNamespace(_mapping={"id": 1, **row})),
                    ],
                )
                self.rollback = AsyncMock()

        now = datetime.now(UTC)
        row = {
            "token": "t" * 32,
            "type": InviteType.CLIENT,
            "max_uses": 1,
            "used_count": 0,
            "expires_at": now,
            "used_at": None,
            "master_id": 1,
            "client_id": None,
            "created_at": now,
        }

        session = _Session()
        repo = InviteRepository(session)
        invite = Invite(type=InviteType.CLIENT, master_id=1, created_at=now, expires_at=now)
        with patch.object(InviteRepository, "_generate_token", side_effect=["x" * 32, "t" * 32]):
            created = await repo.create(invite)

        self.assertEqual(created.id, 1)
        self.assertEqual(created.token, "t" * 32)
        session.rollback.assert_not_awaited()
