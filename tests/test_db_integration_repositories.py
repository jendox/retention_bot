from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.core.sa import Database, active_session
from src.repositories import BookingRepository, ClientRepository, InviteRepository, MasterRepository, WorkdayOverrideRepository
from src.schemas import BookingCreate, ClientCreate, Invite, MasterCreate, WorkdayOverrideCreate
from src.schemas.enums import BookingStatus, InviteType, Timezone


def _env(name: str) -> str | None:
    value = os.getenv(name)
    return value if value and value.strip() else None


@unittest.skipUnless(
    os.getenv("INTEGRATION_TESTS") == "1"
    and _env("DATABASE__POSTGRES_URL") is not None,
    "Set INTEGRATION_TESTS=1 and DATABASE__POSTGRES_URL to run integration tests.",
)
class RepositoryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Apply migrations once per test run (requires alembic + python-dotenv installed).
        from alembic import command
        from alembic.config import Config

        cfg = Config("alembic.ini")
        command.upgrade(cfg, "head")

    async def asyncSetUp(self) -> None:
        postgres_url = os.environ["DATABASE__POSTGRES_URL"]
        self._db_cm = Database.lifespan(url=postgres_url, echo=False)
        await self._db_cm.__aenter__()
        await self._truncate_all()

    async def asyncTearDown(self) -> None:
        await self._db_cm.__aexit__(None, None, None)

    async def _truncate_all(self) -> None:
        async with active_session() as session:
            await session.execute(
                text(
                    "TRUNCATE "
                    "bookings, invites, master_clients, workday_overrides, subscriptions, clients, masters "
                    "RESTART IDENTITY CASCADE;",
                ),
            )

    async def _create_master_and_client(self) -> tuple[int, int]:
        async with active_session() as session:
            masters = MasterRepository(session)
            clients = ClientRepository(session)

            master = await masters.create(
                MasterCreate(
                    telegram_id=1001,
                    name="M",
                    phone="+375291234567",
                    work_days=[0, 1, 2, 3, 4],
                    start_time=time(9, 0),
                    end_time=time(18, 0),
                    slot_size_min=60,
                    timezone=Timezone.EUROPE_MINSK,
                    notify_clients=True,
                ),
            )
            client = await clients.create(
                ClientCreate(
                    telegram_id=2001,
                    name="C",
                    phone="+375291111111",
                    timezone=Timezone.EUROPE_MINSK,
                    notifications_enabled=True,
                ),
            )
            await masters.attach_client(master.id, client.id)
            return master.id, client.id

    async def test_booking_overlap_exclusion_blocks_second_active_booking(self) -> None:
        master_id, client_id = await self._create_master_and_client()
        start = datetime.now(UTC) + timedelta(days=2)
        start = start.replace(minute=0, second=0, microsecond=0)

        async with active_session() as session:
            repo = BookingRepository(session)
            await repo.create(
                BookingCreate(
                    master_id=master_id,
                    client_id=client_id,
                    start_at=start,
                    duration_min=60,
                    status=BookingStatus.CONFIRMED,
                ),
            )

        with self.assertRaises(IntegrityError):
            async with active_session() as session:
                repo = BookingRepository(session)
                await repo.create(
                    BookingCreate(
                        master_id=master_id,
                        client_id=client_id,
                        start_at=start + timedelta(minutes=30),
                        duration_min=60,
                        status=BookingStatus.PENDING,
                    ),
                )

    async def test_cancelled_booking_does_not_block_overlap(self) -> None:
        master_id, client_id = await self._create_master_and_client()
        start = datetime.now(UTC) + timedelta(days=2)
        start = start.replace(minute=0, second=0, microsecond=0)

        async with active_session() as session:
            repo = BookingRepository(session)
            await repo.create(
                BookingCreate(
                    master_id=master_id,
                    client_id=client_id,
                    start_at=start,
                    duration_min=60,
                    status=BookingStatus.CANCELLED,
                ),
            )
            booking2 = await repo.create(
                BookingCreate(
                    master_id=master_id,
                    client_id=client_id,
                    start_at=start,
                    duration_min=60,
                    status=BookingStatus.CONFIRMED,
                ),
            )

        self.assertIsNotNone(booking2.id)

    async def test_invite_increment_used_count_is_atomic(self) -> None:
        async with active_session() as session:
            masters = MasterRepository(session)
            invites = InviteRepository(session)

            master = await masters.create(
                MasterCreate(
                    telegram_id=1001,
                    name="M",
                    phone="+375291234567",
                    work_days=[0, 1, 2, 3, 4],
                    start_time=time(9, 0),
                    end_time=time(18, 0),
                    slot_size_min=60,
                    timezone=Timezone.EUROPE_MINSK,
                    notify_clients=True,
                ),
            )
            invite = await invites.create(Invite(type=InviteType.CLIENT, master_id=master.id))

            ok1 = await invites.increment_used_count_if_valid(invite.token)
            ok2 = await invites.increment_used_count_if_valid(invite.token)

        self.assertTrue(ok1)
        self.assertFalse(ok2)

    async def test_workday_override_unique_per_master_day(self) -> None:
        async with active_session() as session:
            masters = MasterRepository(session)
            overrides = WorkdayOverrideRepository(session)

            master = await masters.create(
                MasterCreate(
                    telegram_id=1001,
                    name="M",
                    phone="+375291234567",
                    work_days=[0, 1, 2, 3, 4],
                    start_time=time(9, 0),
                    end_time=time(18, 0),
                    slot_size_min=60,
                    timezone=Timezone.EUROPE_MINSK,
                    notify_clients=True,
                ),
            )
            await overrides.create(
                WorkdayOverrideCreate(
                    master_id=master.id,
                    date=datetime.now(UTC).date(),
                    start_time=time(10, 0),
                    end_time=time(16, 0),
                ),
            )

        with self.assertRaises(IntegrityError):
            async with active_session() as session:
                overrides = WorkdayOverrideRepository(session)
                await overrides.create(
                    WorkdayOverrideCreate(
                        master_id=master.id,
                        date=datetime.now(UTC).date(),
                        start_time=time(11, 0),
                        end_time=time(17, 0),
                    ),
                )


