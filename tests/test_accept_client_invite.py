from __future__ import annotations

import unittest
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.repositories import ClientNotFound, InviteNotFound
from src.schemas import Client, Invite, Master
from src.schemas.enums import InviteType, Timezone
from src.use_cases.accept_client_invite import (
    AcceptClientInvite,
    AcceptClientInviteRequest,
    AcceptInviteError,
    AcceptInviteOutcome,
)
from src.use_cases.entitlements import EntitlementCheck, Usage


def _now() -> datetime:
    return datetime.now(UTC)


def make_master(*, master_id: int = 1, telegram_id: int = 1001) -> Master:
    now = _now()
    return Master(
        id=master_id,
        telegram_id=telegram_id,
        name="Master",
        phone="+375291234567",
        work_days=[0, 1, 2, 3, 4],
        start_time=time(9, 0),
        end_time=time(18, 0),
        slot_size_min=60,
        timezone=Timezone.EUROPE_MINSK,
        notify_clients=True,
        created_at=now,
        updated_at=now,
    )


def make_client(
    *,
    client_id: int = 1,
    telegram_id: int | None = 2001,
    phone: str = "+375291234567",
    name: str = "Client",
) -> Client:
    now = _now()
    return Client(
        id=client_id,
        telegram_id=telegram_id,
        name=name,
        phone=phone,
        timezone=Timezone.EUROPE_MINSK,
        notifications_enabled=True,
        created_at=now,
        updated_at=now,
    )


def make_invite(
    *,
    token: str = "token",
    master_id: int = 1,
    invite_type: InviteType = InviteType.CLIENT,
    max_uses: int | None = 1,
    used_count: int = 0,
    expires_in: timedelta = timedelta(hours=1),
) -> Invite:
    return Invite(
        token=token,
        type=invite_type,
        max_uses=max_uses,
        used_count=used_count,
        expires_at=_now() + expires_in,
        master_id=master_id,
    )


class AcceptClientInviteTests(unittest.IsolatedAsyncioTestCase):
    def _make_uc(self) -> AcceptClientInvite:
        uc = AcceptClientInvite(session=AsyncMock())
        uc._invite_repo = SimpleNamespace(
            get_by_token=AsyncMock(),
            increment_used_count_if_valid=AsyncMock(),
        )
        uc._client_repo = SimpleNamespace(
            get_by_telegram_id=AsyncMock(),
            find_for_master_by_phone=AsyncMock(),
            update_by_id=AsyncMock(),
            create=AsyncMock(),
        )
        uc._master_repo = SimpleNamespace(
            get_by_id=AsyncMock(),
            is_client_attached=AsyncMock(return_value=False),
            attach_client=AsyncMock(),
            detach_client=AsyncMock(),
            set_client_alias_if_empty=AsyncMock(),
            count_clients=AsyncMock(return_value=0),
        )
        uc._booking_repo = SimpleNamespace(
            reassign_client_for_master=AsyncMock(return_value=0),
        )
        uc._entitlements = SimpleNamespace(
            can_attach_client=AsyncMock(
                return_value=EntitlementCheck(
                    allowed=True,
                    reason=None,
                    current=0,
                    limit=10,
                    remaining=10,
                ),
            ),
            near_limits=AsyncMock(return_value=set()),
            get_usage=AsyncMock(return_value=Usage(clients_count=0, bookings_created_this_month=0)),
        )
        uc._outbox = SimpleNamespace(
            cancel_onboarding_for_master=AsyncMock(return_value=0),
            schedule_master_onboarding_add_first_booking=AsyncMock(return_value=0),
        )
        return uc

    async def test_invite_not_found(self) -> None:
        uc = self._make_uc()
        uc._invite_repo.get_by_token.side_effect = InviteNotFound()

        result = await uc.execute(AcceptClientInviteRequest(telegram_id=1, invite_token="missing"))

        self.assertFalse(result.ok)
        self.assertEqual(result.error, AcceptInviteError.INVITE_NOT_FOUND)

    async def test_invite_wrong_type(self) -> None:
        uc = self._make_uc()
        uc._invite_repo.get_by_token.return_value = make_invite(invite_type=InviteType.MASTER)

        result = await uc.execute(AcceptClientInviteRequest(telegram_id=1, invite_token="t"))

        self.assertFalse(result.ok)
        self.assertEqual(result.error, AcceptInviteError.INVITE_WRONG_TYPE)

    async def test_invite_master_mismatch(self) -> None:
        uc = self._make_uc()
        uc._invite_repo.get_by_token.return_value = make_invite(master_id=1)

        result = await uc.execute(
            AcceptClientInviteRequest(
                telegram_id=1,
                invite_token="t",
                expected_master_id=2,
            ),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, AcceptInviteError.INVITE_MASTER_MISMATCH)
        self.assertEqual(result.master_id, 1)
        uc._master_repo.get_by_id.assert_not_awaited()

    async def test_missing_phone_new_client(self) -> None:
        uc = self._make_uc()
        uc._invite_repo.get_by_token.return_value = make_invite(master_id=1)
        uc._master_repo.get_by_id.return_value = make_master(master_id=1, telegram_id=42)
        uc._client_repo.get_by_telegram_id.side_effect = ClientNotFound()

        result = await uc.execute(AcceptClientInviteRequest(telegram_id=1, invite_token="t"))

        self.assertFalse(result.ok)
        self.assertEqual(result.error, AcceptInviteError.MISSING_PHONE)
        self.assertEqual(result.master_id, 1)
        self.assertEqual(result.master_telegram_id, 42)

    async def test_missing_phone_existing_client_has_no_phone(self) -> None:
        uc = self._make_uc()
        uc._invite_repo.get_by_token.return_value = make_invite(master_id=1)
        uc._master_repo.get_by_id.return_value = make_master(master_id=1, telegram_id=42)
        uc._client_repo.get_by_telegram_id.return_value = SimpleNamespace(id=1, telegram_id=1, phone=None)

        result = await uc.execute(AcceptClientInviteRequest(telegram_id=1, invite_token="t"))

        self.assertFalse(result.ok)
        self.assertEqual(result.error, AcceptInviteError.MISSING_PHONE)

    async def test_phone_conflict_client_for_phone_bound_to_other_tg(self) -> None:
        uc = self._make_uc()
        uc._invite_repo.get_by_token.return_value = make_invite(master_id=1)
        uc._master_repo.get_by_id.return_value = make_master(master_id=1, telegram_id=42)
        uc._client_repo.get_by_telegram_id.side_effect = ClientNotFound()
        uc._client_repo.find_for_master_by_phone.return_value = make_client(client_id=10, telegram_id=999)

        result = await uc.execute(
            AcceptClientInviteRequest(
                telegram_id=1,
                invite_token="t",
                phone_e164="+375291234567",
            ),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, AcceptInviteError.PHONE_CONFLICT)
        uc._entitlements.can_attach_client.assert_not_awaited()

    async def test_phone_conflict_existing_client_phone_taken_by_other_online_client(self) -> None:
        uc = self._make_uc()
        uc._invite_repo.get_by_token.return_value = make_invite(master_id=1)
        uc._master_repo.get_by_id.return_value = make_master(master_id=1, telegram_id=42)
        existing = make_client(client_id=1, telegram_id=1, phone="+375111111111")
        uc._client_repo.get_by_telegram_id.return_value = existing

        async def _find_for_master_by_phone(*, master_id: int, phone: str):
            if phone == "+375222222222":
                raise ClientNotFound()
            return make_client(client_id=2, telegram_id=999, phone=phone)

        uc._client_repo.find_for_master_by_phone.side_effect = _find_for_master_by_phone

        result = await uc.execute(
            AcceptClientInviteRequest(
                telegram_id=1,
                invite_token="t",
                phone_e164="+375222222222",
            ),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, AcceptInviteError.PHONE_CONFLICT)

    async def test_quota_exceeded(self) -> None:
        uc = self._make_uc()
        uc._invite_repo.get_by_token.return_value = make_invite(master_id=1)
        uc._master_repo.get_by_id.return_value = make_master(master_id=1, telegram_id=42)
        uc._client_repo.get_by_telegram_id.side_effect = ClientNotFound()
        uc._client_repo.find_for_master_by_phone.side_effect = ClientNotFound()
        uc._entitlements.can_attach_client.return_value = EntitlementCheck(
            allowed=False,
            reason="clients_limit_reached",
            current=3,
            limit=3,
            remaining=0,
        )

        result = await uc.execute(
            AcceptClientInviteRequest(telegram_id=1, invite_token="t", phone_e164="+375291234567"),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, AcceptInviteError.QUOTA_EXCEEDED)
        uc._invite_repo.increment_used_count_if_valid.assert_not_awaited()

    async def test_invite_invalid_on_consume(self) -> None:
        uc = self._make_uc()
        uc._invite_repo.get_by_token.return_value = make_invite(master_id=1)
        uc._invite_repo.increment_used_count_if_valid.return_value = False
        uc._master_repo.get_by_id.return_value = make_master(master_id=1, telegram_id=42)
        uc._client_repo.get_by_telegram_id.side_effect = ClientNotFound()
        uc._client_repo.find_for_master_by_phone.side_effect = ClientNotFound()

        result = await uc.execute(
            AcceptClientInviteRequest(telegram_id=1, invite_token="t", phone_e164="+375291234567"),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, AcceptInviteError.INVITE_INVALID)
        uc._client_repo.create.assert_not_awaited()

    async def test_noop_already_attached_burns_single_use_invite(self) -> None:
        uc = self._make_uc()
        invite = make_invite(master_id=1, max_uses=1, used_count=0)
        uc._invite_repo.get_by_token.return_value = invite
        uc._invite_repo.increment_used_count_if_valid.return_value = True
        uc._master_repo.get_by_id.return_value = make_master(master_id=1, telegram_id=42)
        existing = make_client(client_id=1, telegram_id=1, phone="+375291234567")
        uc._client_repo.get_by_telegram_id.return_value = existing
        uc._client_repo.find_for_master_by_phone.side_effect = ClientNotFound()
        uc._master_repo.is_client_attached.return_value = True

        result = await uc.execute(
            AcceptClientInviteRequest(telegram_id=1, invite_token="t"),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.outcome, AcceptInviteOutcome.ATTACHED_EXISTING)
        self.assertTrue(result.warn_master_clients_near_limit is False)
        uc._invite_repo.increment_used_count_if_valid.assert_awaited()
        uc._master_repo.attach_client.assert_not_awaited()
        uc._entitlements.near_limits.assert_not_awaited()

    async def test_noop_already_attached_burn_failed(self) -> None:
        uc = self._make_uc()
        invite = make_invite(master_id=1, max_uses=1, used_count=0)
        uc._invite_repo.get_by_token.return_value = invite
        uc._invite_repo.increment_used_count_if_valid.return_value = False
        uc._master_repo.get_by_id.return_value = make_master(master_id=1, telegram_id=42)
        existing = make_client(client_id=1, telegram_id=1, phone="+375291234567")
        uc._client_repo.get_by_telegram_id.return_value = existing
        uc._client_repo.find_for_master_by_phone.side_effect = ClientNotFound()
        uc._master_repo.is_client_attached.return_value = True

        result = await uc.execute(AcceptClientInviteRequest(telegram_id=1, invite_token="t"))

        self.assertTrue(result.ok)
        self.assertEqual(result.outcome, AcceptInviteOutcome.ATTACHED_EXISTING)
        uc._master_repo.attach_client.assert_not_awaited()

    async def test_merge_offline_into_existing(self) -> None:
        uc = self._make_uc()
        uc._invite_repo.get_by_token.return_value = make_invite(master_id=1)
        uc._invite_repo.increment_used_count_if_valid.return_value = True
        uc._master_repo.get_by_id.return_value = make_master(master_id=1, telegram_id=42)
        existing = make_client(client_id=1, telegram_id=1, phone="+375291234567")
        offline = make_client(client_id=2, telegram_id=None, phone="+375291234567")
        uc._client_repo.get_by_telegram_id.return_value = existing
        uc._client_repo.find_for_master_by_phone.return_value = offline
        uc._booking_repo.reassign_client_for_master.return_value = 5

        result = await uc.execute(AcceptClientInviteRequest(telegram_id=1, invite_token="t", phone_e164=offline.phone))

        self.assertTrue(result.ok)
        self.assertEqual(result.outcome, AcceptInviteOutcome.MERGED_OFFLINE)
        self.assertEqual(result.client_id, existing.id)
        uc._master_repo.set_client_alias_if_empty.assert_awaited_with(
            master_id=1,
            client_id=existing.id,
            alias=offline.name,
        )
        uc._booking_repo.reassign_client_for_master.assert_awaited_with(
            master_id=1,
            from_client_id=offline.id,
            to_client_id=existing.id,
        )
        uc._master_repo.detach_client.assert_awaited_with(1, offline.id)
        uc._master_repo.attach_client.assert_awaited_with(1, existing.id)

    async def test_attach_existing_client(self) -> None:
        uc = self._make_uc()
        uc._invite_repo.get_by_token.return_value = make_invite(master_id=1)
        uc._invite_repo.increment_used_count_if_valid.return_value = True
        uc._master_repo.get_by_id.return_value = make_master(master_id=1, telegram_id=42)
        existing = make_client(client_id=1, telegram_id=1, phone="+375291234567")
        uc._client_repo.get_by_telegram_id.return_value = existing
        uc._client_repo.find_for_master_by_phone.side_effect = ClientNotFound()

        result = await uc.execute(AcceptClientInviteRequest(telegram_id=1, invite_token="t"))

        self.assertTrue(result.ok)
        self.assertEqual(result.outcome, AcceptInviteOutcome.ATTACHED_EXISTING)
        uc._master_repo.attach_client.assert_awaited_with(1, existing.id)

    async def test_claim_offline_client(self) -> None:
        uc = self._make_uc()
        uc._invite_repo.get_by_token.return_value = make_invite(master_id=1)
        uc._invite_repo.increment_used_count_if_valid.return_value = True
        uc._master_repo.get_by_id.return_value = make_master(master_id=1, telegram_id=42)
        uc._client_repo.get_by_telegram_id.side_effect = ClientNotFound()
        offline = make_client(client_id=2, telegram_id=None, phone="+375291234567", name="Offline")
        uc._client_repo.find_for_master_by_phone.return_value = offline

        result = await uc.execute(
            AcceptClientInviteRequest(
                telegram_id=1,
                invite_token="t",
                phone_e164=offline.phone,
                name="New Name",
            ),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.outcome, AcceptInviteOutcome.CLAIMED_OFFLINE)
        uc._master_repo.set_client_alias_if_empty.assert_awaited_with(
            master_id=1,
            client_id=offline.id,
            alias="Offline",
        )
        uc._client_repo.update_by_id.assert_awaited()
        uc._master_repo.attach_client.assert_awaited_with(1, offline.id)

    async def test_create_new_client(self) -> None:
        uc = self._make_uc()
        uc._invite_repo.get_by_token.return_value = make_invite(master_id=1)
        uc._invite_repo.increment_used_count_if_valid.return_value = True
        uc._master_repo.get_by_id.return_value = make_master(master_id=1, telegram_id=42)
        uc._client_repo.get_by_telegram_id.side_effect = ClientNotFound()
        uc._client_repo.find_for_master_by_phone.side_effect = ClientNotFound()
        created = make_client(client_id=123, telegram_id=1, phone="+375291234567")
        uc._client_repo.create.return_value = created

        result = await uc.execute(
            AcceptClientInviteRequest(telegram_id=1, invite_token="t", phone_e164=created.phone, name="A"),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.outcome, AcceptInviteOutcome.CREATED)
        self.assertEqual(result.client_id, created.id)
        uc._master_repo.attach_client.assert_awaited_with(1, created.id)

    async def test_warn_near_clients_limit_fetches_usage(self) -> None:
        uc = self._make_uc()
        uc._invite_repo.get_by_token.return_value = make_invite(master_id=1)
        uc._invite_repo.increment_used_count_if_valid.return_value = True
        uc._master_repo.get_by_id.return_value = make_master(master_id=1, telegram_id=42)
        uc._client_repo.get_by_telegram_id.side_effect = ClientNotFound()
        uc._client_repo.find_for_master_by_phone.side_effect = ClientNotFound()
        created = make_client(client_id=123, telegram_id=1, phone="+375291234567")
        uc._client_repo.create.return_value = created
        uc._entitlements.near_limits.return_value = {"clients"}
        usage = Usage(clients_count=8, bookings_created_this_month=0)
        uc._entitlements.get_usage.return_value = usage

        result = await uc.execute(
            AcceptClientInviteRequest(telegram_id=1, invite_token="t", phone_e164=created.phone, name="A"),
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.warn_master_clients_near_limit)
        self.assertEqual(result.usage, usage)
        uc._entitlements.get_usage.assert_awaited_with(master_id=1)
