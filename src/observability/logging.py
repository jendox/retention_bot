from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import UTC, datetime

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


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }

        if record.exc_info:
            payload["exception"] = "".join(traceback.format_exception(*record.exc_info)).strip()

        extras = {
            k: v for k, v in record.__dict__.items() if k not in _RESERVED_ATTRS and not k.startswith("_")
        }
        for k, v in extras.items():
            if k in payload:
                payload[f"extra_{k}"] = v
            else:
                payload[k] = v

        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(*, debug: bool) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setLevel(logging.DEBUG if debug else logging.INFO)
    handler.setFormatter(JsonFormatter())
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
