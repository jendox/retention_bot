from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch


@asynccontextmanager
async def _fake_session_local():
    yield object()


class ClientListBookingsAliasTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_client_bookings_applies_master_alias(self) -> None:
        from src.handlers.client import list_bookings as lb

        client = SimpleNamespace(id=1, timezone=SimpleNamespace(value="Europe/Minsk"))
        booking = SimpleNamespace(master=SimpleNamespace(id=10, name="Profile"))

        class _ClientRepo:
            async def get_by_telegram_id(self, telegram_id: int):  # noqa: ARG002
                return client

        class _BookingRepo:
            async def get_for_client(self, **kwargs):  # noqa: ARG002
                return [booking]

        class _MasterRepo:
            async def get_master_aliases_for_client(self, *, client_id: int) -> dict[int, str]:  # noqa: ARG002
                return {10: "Alias"}

        with (
            patch.object(lb, "session_local", _fake_session_local),
            patch.object(lb, "ClientRepository", lambda session: _ClientRepo()),  # noqa: ARG005
            patch.object(lb, "BookingRepository", lambda session: _BookingRepo()),  # noqa: ARG005
            patch.object(lb, "MasterClientRepository", lambda session: _MasterRepo()),  # noqa: ARG005
        ):
            result = await lb._fetch_client_bookings(telegram_id=777)

        assert result is not None
        _tz, bookings = result
        self.assertEqual(bookings[0].master.name, "Alias")
