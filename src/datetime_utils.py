from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def get_timezone(tz_name: str) -> ZoneInfo:
    """Возвращает объект ZoneInfo для указанного часового пояса"""
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def utc_to_local(dt: datetime, tz_name: str) -> datetime:
    """Перевод UTC → локальное время"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(get_timezone(tz_name))


def local_to_utc(dt: datetime, tz_name: str) -> datetime:
    """Перевод локального времени → UTC"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=get_timezone(tz_name))
    return dt.astimezone(UTC)


def format_dt_local(dt: datetime, tz_name: str, fmt: str = "%d.%m.%Y %H:%M") -> str:
    """Форматирует UTC datetime в строку в локальном времени мастера"""
    return utc_to_local(dt, tz_name).strftime(fmt)
