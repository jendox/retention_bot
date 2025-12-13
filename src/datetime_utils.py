from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.schemas.enums import Timezone


@dataclass(frozen=True)
class UtcRange:
    start: datetime
    end: datetime


def get_timezone(tz_name: str) -> ZoneInfo:
    """Возвращает объект ZoneInfo для указанного часового пояса"""
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def master_day_from_client_day(*, client_day: date, client_tz: Timezone, master_tz: Timezone) -> date:
    client_zone = ZoneInfo(str(client_tz.value))
    master_zone = ZoneInfo(str(master_tz.value))

    client_midnight = datetime.combine(client_day, time(0, 0), tzinfo=client_zone)
    return client_midnight.astimezone(master_zone).date()


def utc_range_for_master_day(*, master_day: date, master_tz: Timezone) -> UtcRange:
    master_zone = ZoneInfo(str(master_tz.value))
    start_local = datetime.combine(master_day, time(0, 0), tzinfo=master_zone)
    end_local = start_local + timedelta(days=1)
    return UtcRange(start=start_local.astimezone(UTC), end=end_local.astimezone(UTC))


def to_zone(dt_utc: datetime, tz: Timezone) -> datetime:
    return dt_utc.astimezone(ZoneInfo(str(tz.value)))
