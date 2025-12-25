from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Iterable
from typing import Any

from aiogram import Bot
from redis.asyncio import Redis

from src.settings import get_settings
from src.utils import notify_admins

logger = logging.getLogger(__name__)

_SENSITIVE_KEYS = {
    "token",
    "secret",
    "password",
    "authorization",
    "invite",
    "invite_token",
    "invite_secret",
    "bot_token",
}


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEYS)


def _safe_repr(value: Any, *, limit: int = 500) -> str:
    try:
        rendered = str(value)
    except Exception:
        rendered = repr(value)
    if len(rendered) > limit:
        return rendered[:limit] + "…"
    return rendered


def _sanitize_dict(data: dict[str, Any] | None) -> dict[str, str]:
    if not data:
        return {}
    out: dict[str, str] = {}
    for key, value in data.items():
        if value is None:
            continue
        if _is_sensitive_key(key):
            out[key] = "<redacted>"
            continue
        out[key] = _safe_repr(value)
    return out


class AdminAlerter:
    """
    Best-effort admin alerting with throttling (Redis if available, otherwise in-memory).
    """

    def __init__(
        self,
        *,
        bot: Bot,
        admin_ids: Iterable[int],
        redis: Redis | None = None,
        enabled: bool = True,
        default_throttle_sec: int = 10 * 60,
    ) -> None:
        self._bot = bot
        self._admin_ids = {int(v) for v in admin_ids}
        self._redis = redis
        self._enabled = enabled
        self._default_throttle_sec = int(default_throttle_sec)
        self._in_memory: dict[str, float] = {}

    async def notify(
        self,
        *,
        event: str,
        text: str,
        level: str = "ERROR",
        throttle_key: str | None = None,
        throttle_sec: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        if not self._enabled or not self._admin_ids:
            return False
        settings = get_settings()
        allowlist = settings.observability.alerts_events
        if allowlist is not None and event not in allowlist:
            return False

        throttle_sec = int(throttle_sec or self._default_throttle_sec)
        dedup_key = throttle_key or event
        dedup_key_h = hashlib.sha1(dedup_key.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
        storage_key = f"alert:{dedup_key_h}"

        allowed = await self._allow(storage_key, throttle_sec=throttle_sec)
        if not allowed:
            logger.debug("admin_alert.suppressed", extra={"event": event, "storage_key": storage_key})
            return False

        safe_extra = _sanitize_dict(extra)
        message = self._format_message(event=event, level=level, text=text, extra=safe_extra)

        await notify_admins(self._bot, self._admin_ids, message)
        logger.info("admin_alert.sent", extra={"event": event, "storage_key": storage_key})
        return True

    async def _allow(self, key: str, *, throttle_sec: int) -> bool:
        now = time.monotonic()
        expires_at = self._in_memory.get(key)
        if expires_at is not None and expires_at > now:
            return False

        if self._redis is None:
            self._in_memory[key] = now + throttle_sec
            return True

        try:
            ok = await self._redis.set(name=key, value="1", ex=throttle_sec, nx=True)
        except Exception:
            logger.warning("admin_alert.redis_error", exc_info=True)
            self._in_memory[key] = now + throttle_sec
            return True

        if ok:
            self._in_memory[key] = now + throttle_sec
            return True
        return False

    @staticmethod
    def _format_message(*, event: str, level: str, text: str, extra: dict[str, str]) -> str:
        lines = [f"[{level}] {event}", text]
        for k in sorted(extra):
            lines.append(f"{k}={extra[k]}")
        return "\n".join(lines)
