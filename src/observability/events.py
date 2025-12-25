from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from typing import Any

from src.observability.context import get_log_context
from src.observability.policy import AlertPolicy, AlertSpec
from src.settings import get_settings


def _drop_none(fields: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in fields.items() if v is not None}


def _safe_error_message(exc: Exception | None, *, limit: int = 300) -> str | None:
    if exc is None:
        return None
    try:
        text = str(exc)
    except Exception:
        text = repr(exc)
    if len(text) > limit:
        return text[:limit] + "…"
    return text


class EventLogger:
    """
    Thin helper over stdlib logging for consistent structured events.

    Keys convention (recommended):
    - `event`: string identifier (passed as the log message)
    - `outcome`: domain result code (string)
    - `stage`: failing sub-stage ("db", "redis", "use_case", ...)
    - `reason`: short reason code for invalid inputs/branches
    - `duration_ms`: integer duration in milliseconds
    - `scope`: rate-limiter / feature scope identifier
    - `error_type`: exception class name (for failures)
    - `error`: short exception message (trimmed)
    """

    def __init__(self, name: str, *, policy: AlertPolicy | None = None) -> None:
        self._logger = logging.getLogger(name)
        self._policy = policy or AlertPolicy()

    def _should_sample(self, *, event: str) -> bool:
        settings = get_settings()
        rate = settings.observability.log_sample_rate_by_event.get(event)
        if rate is None:
            return True
        try:
            rate_f = float(rate)
        except (TypeError, ValueError):
            return True
        if rate_f >= 1:
            return True
        if rate_f <= 0:
            return False

        ctx = get_log_context()
        trace_id = str(ctx.get("trace_id") or "")
        if trace_id:
            h = hashlib.sha1(f"{event}:{trace_id}".encode(), usedforsecurity=False).digest()
            bucket = int.from_bytes(h[:2], "big") / 65535.0
            return bucket < rate_f
        # No trace_id: do a stable hash on the event only (better than random for reproducibility).
        h = hashlib.sha1(event.encode("utf-8"), usedforsecurity=False).digest()
        bucket = int.from_bytes(h[:2], "big") / 65535.0
        return bucket < rate_f

    def debug(self, event: str, **fields: Any) -> None:
        if self._should_sample(event=event):
            self._logger.debug(event, extra=_drop_none(fields))

    def info(self, event: str, **fields: Any) -> None:
        if self._should_sample(event=event):
            self._logger.info(event, extra=_drop_none(fields))

    def warning(self, event: str, **fields: Any) -> None:
        if self._should_sample(event=event):
            self._logger.warning(event, extra=_drop_none(fields))

    def error(self, event: str, **fields: Any) -> None:
        # Never sample errors by default; if callers want sampling they should log at INFO/DEBUG.
        self._logger.error(event, extra=_drop_none(fields))

    def exception(self, event: str, **fields: Any) -> None:
        self._logger.exception(event, extra=_drop_none(fields))

    async def maybe_alert(self, *, event: str, level: str, fields: dict[str, Any], admin_alerter: Any | None) -> bool:
        if admin_alerter is None:
            return False
        settings = get_settings()
        if not settings.observability.alerts_enabled:
            return False
        allowlist = settings.observability.alerts_events
        if allowlist is not None and event not in allowlist:
            return False

        spec = self._policy.decide(event=event, level=level, fields=fields)
        if spec is None:
            # Allow alerting on new events without code changes if allowlist is configured.
            if allowlist is None:
                return False
            spec = AlertSpec(
                level=settings.observability.alerts_level_by_event.get(event, "ERROR"),
                throttle_key=event,
                throttle_sec=settings.observability.alerts_throttle_sec_by_event.get(
                    event,
                    settings.observability.alerts_default_throttle_sec,
                ),
                text=f"Alert: {event}",
            )

        ctx = get_log_context()
        merged = {**ctx, **fields}
        throttle_sec = settings.observability.alerts_throttle_sec_by_event.get(event, spec.throttle_sec)
        level_override = settings.observability.alerts_level_by_event.get(event)
        level_to_send = level_override or spec.level
        text_override = settings.observability.alerts_text_by_event.get(event)
        text_to_send = text_override or spec.text
        return await admin_alerter.notify(
            event=event,
            text=text_to_send,
            level=level_to_send,
            throttle_key=spec.throttle_key,
            throttle_sec=throttle_sec,
            extra=merged,
        )

    async def aerror(self, event: str, *, admin_alerter: Any | None = None, **fields: Any) -> None:
        extra = _drop_none(fields)
        self._logger.error(event, extra=extra)
        await self.maybe_alert(event=event, level="ERROR", fields=extra, admin_alerter=admin_alerter)

    async def aexception(
        self,
        event: str,
        *,
        exc: Exception | None = None,
        admin_alerter: Any | None = None,
        **fields: Any,
    ) -> None:
        extra = _drop_none(
            {
                **fields,
                "error_type": type(exc).__name__ if exc is not None else None,
                "error": _safe_error_message(exc),
            },
        )
        self._logger.exception(event, extra=extra)
        await self.maybe_alert(event=event, level="ERROR", fields=extra, admin_alerter=admin_alerter)
