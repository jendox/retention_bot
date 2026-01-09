from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class WorkerDisplayNamesNoCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_display_names_reads_fresh_each_call(self) -> None:
        from src.workers import reminders as r

        session = SimpleNamespace()
        session.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(one_or_none=lambda: (None, None)),
                SimpleNamespace(one_or_none=lambda: ("M alias", "C alias")),
            ],
        )

        @asynccontextmanager
        async def _fake_session_local():
            yield session

        with patch.object(r, "session_local", _fake_session_local):
            first = await r._resolve_display_names(master_id=1, client_id=2)
            second = await r._resolve_display_names(master_id=1, client_id=2)

        self.assertEqual(first, (None, None))
        self.assertEqual(second, ("M alias", "C alias"))
