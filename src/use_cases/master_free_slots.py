from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.datetime_utils import master_day_from_client_day, to_zone, utc_range_for_master_day
from src.repositories import BookingRepository, MasterRepository
from src.schedule import get_free_slots_for_date
from src.schemas.enums import BookingStatus, Timezone


@dataclass(frozen=True)
class FreeSlotsResult:
    slots_utc: list[datetime]
    slots_for_client: list[datetime]
    master_day: date


class GetMasterFreeSlots:
    def __init__(self, session: AsyncSession) -> None:
        self._master_repo = MasterRepository(session)
        self._booking_repo = BookingRepository(session)

    async def execute(
        self,
        *,
        master_id,
        client_day: date,
        client_tz: Timezone,
    ) -> FreeSlotsResult:
        master = await self._master_repo.get_for_schedule_by_id(master_id)
        master_day = master_day_from_client_day(
            client_day=client_day,
            client_tz=client_tz,
            master_tz=master.timezone,
        )
        utc_range = utc_range_for_master_day(master_day=master_day, master_tz=master.timezone)

        bookings = await self._booking_repo.get_for_master_in_range(
            master_id=master_id,
            start_at_utc=utc_range.start,
            end_at_utc=utc_range.end,
            statuses=BookingStatus.active(),
        )
        free_slots_master_tz = get_free_slots_for_date(
            master=master,
            target_date=master_day,
            bookings=bookings,
        )
        slots_utc = [dt.astimezone(UTC) for dt in free_slots_master_tz]
        slots_for_client = [to_zone(dt_utc, client_tz) for dt_utc in slots_utc]

        return FreeSlotsResult(slots_utc=slots_utc, slots_for_client=slots_for_client, master_day=master_day)
