from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ExpressPayErrorPayload:
    code: int
    msg: str
    msg_code: int


class ExpressPayError(Exception):
    """Базовая ошибка библиотеки."""


class ExpressPayApiError(ExpressPayError):
    """Ошибка, пришедшая от ExpressPay (узел Error в ответе)."""

    def __init__(self, payload: ExpressPayErrorPayload, *, raw: Any | None = None) -> None:
        super().__init__(f"ExpressPay API error {payload.code}/{payload.msg_code}: {payload.msg}")
        self.payload = payload
        self.raw = raw


class ExpressPayTransportError(ExpressPayError):
    """Сеть/таймаут/невалидный ответ."""
