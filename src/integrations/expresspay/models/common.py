from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ErrorNode(BaseModel):
    Code: int
    Msg: str
    MsgCode: int


class Envelope(BaseModel):
    """
    Любой ответ либо "успешный payload", либо {"Error": {...}}.
    """
    model_config = ConfigDict(extra="allow")
    Error: ErrorNode | None = None


class BoolResult(BaseModel):
    """
    Для методов без явного payload: считаем успех, если нет Error.
    """
    ok: bool = Field(True)
