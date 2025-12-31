from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from typing import Any

from src.observability.context import get_log_context
from src.repositories.audit_log import AuditLogRepository

_REDACTED = "<redacted>"

_SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "authorization",
    "api_key",
    "apikey",
    "invite",
    "phone",
    "msisdn",
)


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return _to_jsonable(getattr(value, "value", None) or str(value))
    if isinstance(value, Mapping):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(v) for v in value]
    return str(value)


def _sanitize_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not metadata:
        return None

    out: dict[str, Any] = {}
    for key, value in metadata.items():
        lowered = str(key).lower()
        if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
            out[str(key)] = _REDACTED
            continue
        out[str(key)] = _to_jsonable(value)
    return out or None


def write_audit_log(  # noqa: PLR0913
    session: Any,
    *,
    event: str,
    actor: str | None = None,
    actor_id: int | None = None,
    master_id: int | None = None,
    client_id: int | None = None,
    booking_id: int | None = None,
    invite_id: int | None = None,
    invoice_id: int | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    if not hasattr(session, "add"):
        return
    ctx = get_log_context()
    trace_id = ctx.get("trace_id") if ctx else None

    AuditLogRepository(session).add(
        event=event,
        actor=actor,
        actor_id=actor_id,
        master_id=master_id,
        client_id=client_id,
        booking_id=booking_id,
        invite_id=invite_id,
        invoice_id=invoice_id,
        trace_id=str(trace_id) if trace_id is not None else None,
        metadata=_sanitize_metadata(metadata),
    )
