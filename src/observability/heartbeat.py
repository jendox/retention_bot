from __future__ import annotations

from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from src.observability.events import EventLogger

HEARTBEAT_PREFIX = "beautydesk:heartbeat:"


def heartbeat_key(worker: str) -> str:
    return f"{HEARTBEAT_PREFIX}{worker}"


async def write_worker_heartbeat(
    redis: Redis,
    *,
    worker: str,
    ttl: timedelta,
    now_utc: datetime | None = None,
    ev: EventLogger | None = None,
) -> None:
    now_utc = now_utc or datetime.now(UTC)
    try:
        await redis.set(
            name=heartbeat_key(worker),
            value=str(int(now_utc.timestamp())),
            ex=int(ttl.total_seconds()),
        )
    except Exception as exc:
        if ev is None:
            return
        ev.warning(
            f"workers.{worker}.heartbeat_write_failed",
            error_type=type(exc).__name__,
        )
