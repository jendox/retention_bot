from __future__ import annotations

import json
import logging
import re
import sys
import traceback
from datetime import UTC, datetime

from src.observability.context import get_log_context

_RESERVED_ATTRS: set[str] = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}

_REDACTED = "<redacted>"

# Keep this intentionally broad; it's a last line of defense against accidental PII leakage.
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

# E.164-ish (best-effort). We prefer occasional over-redaction to accidental leakage.
_PHONE_RE = re.compile(r"(?<!\\d)(?:\\+\\d{11,15}|375\\d{9})(?!\\d)")

# Common "key=value" patterns that show up in errors/exceptions.
_KV_PHONE_RE = re.compile(r"(?i)\\bphone\\s*=\\s*(?:\\+?\\d{11,15}|375\\d{9})\\b")
_KV_INVITE_TOKEN_RE = re.compile(r"(?i)\\binvite_token\\s*=\\s*[^\\s,;]+")


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _redact_text(text: str) -> str:
    out = _PHONE_RE.sub(_REDACTED, text)
    out = _KV_PHONE_RE.sub(f"phone={_REDACTED}", out)
    out = _KV_INVITE_TOKEN_RE.sub(f"invite_token={_REDACTED}", out)
    return out


def _redact_value(key: str, value: object) -> object:
    if value is None:
        return None
    if _is_sensitive_key(key):
        return _REDACTED
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_payload(payload: dict[str, object]) -> dict[str, object]:
    out: dict[str, object] = {}
    for k, v in payload.items():
        out[k] = _redact_value(str(k), v)
    return out


class JsonFormatter(logging.Formatter):
    def __init__(
        self,
        *,
        service: str | None = None,
        env: str | None = None,
        version: str | None = None,
    ) -> None:
        super().__init__()
        base: dict[str, object] = {}
        if service:
            base["service"] = service
        if env:
            base["env"] = env
        if version:
            base["version"] = version
        self._base = base

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            **self._base,
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }

        ctx = get_log_context()
        if ctx:
            for k, v in ctx.items():
                if k in payload:
                    payload[f"ctx_{k}"] = v
                else:
                    payload[k] = v

        if record.exc_info:
            payload["exception"] = _redact_text("".join(traceback.format_exception(*record.exc_info)).strip())

        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED_ATTRS and not k.startswith("_")}
        for k, v in extras.items():
            if k in payload:
                payload[f"extra_{k}"] = v
            else:
                payload[k] = v

        return json.dumps(_redact_payload(payload), ensure_ascii=False, default=str)


def setup_logging(
    *,
    debug: bool,
    service: str | None = None,
    env: str | None = None,
    version: str | None = None,
) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setLevel(logging.DEBUG if debug else logging.INFO)
    handler.setFormatter(JsonFormatter(service=service, env=env, version=version))
    root.addHandler(handler)

    # Keep dependencies quieter in production; debug enables them.
    noisy = [
        "aiogram.event",
        "aiogram.middlewares",
        "aiogram.dispatcher",
        "aiohttp.access",
        "urllib3",
    ]
    for name in noisy:
        logging.getLogger(name).setLevel(logging.DEBUG if debug else logging.WARNING)
