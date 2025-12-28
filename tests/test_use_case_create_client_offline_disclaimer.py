from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock


class CreateClientOfflineDisclaimerTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_marks_disclaimer_shown_once(self) -> None:
        import src.use_cases.create_client_offline as uc

        class _MasterRepo:
            def __init__(self, session) -> None:
                pass

            async def get_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(id=1, offline_client_disclaimer_shown=False)

            mark_offline_client_disclaimer_shown = mock.AsyncMock(return_value=True)

        class _Entitlements:
            def __init__(self, session) -> None:
                pass

            async def get_plan(self, *, master_id: int, now=None):
                return SimpleNamespace(is_pro=True)

        with (
            mock.patch.object(uc, "MasterRepository", _MasterRepo),
            mock.patch.object(uc, "EntitlementsService", _Entitlements),
        ):
            result = await uc.CreateClientOffline(session=object()).preflight(telegram_master_id=123)

        self.assertTrue(result.ok)
        self.assertTrue(result.allowed)
        self.assertTrue(result.show_offline_client_disclaimer)
        _MasterRepo.mark_offline_client_disclaimer_shown.assert_awaited_once_with(1)
