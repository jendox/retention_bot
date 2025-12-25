from __future__ import annotations

import secrets
from contextvars import ContextVar, Token
from typing import Any

_log_context: ContextVar[dict[str, Any] | None] = ContextVar("log_context", default=None)


def new_trace_id() -> str:
    # 96 bits, URL-safe, short enough for scanning in logs.
    return secrets.token_urlsafe(12)


def get_log_context() -> dict[str, Any]:
    return dict(_log_context.get() or {})


def set_log_context(context: dict[str, Any]) -> Token:
    return _log_context.set(dict(context))


def clear_log_context() -> Token:
    return _log_context.set({})


def reset_log_context(token: Token) -> None:
    _log_context.reset(token)


def bind_log_context(**fields: Any) -> None:
    current = dict(_log_context.get() or {})
    for key, value in fields.items():
        if value is None:
            continue
        current[key] = value
    _log_context.set(current)
