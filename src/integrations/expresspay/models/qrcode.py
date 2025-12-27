from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class QrViewType(StrEnum):
    base64 = "base64"
    text = "text"


class QrCodeResponse(BaseModel):
    QrCodeBody: str


class QrCodeRequest(BaseModel):
    invoice_id: int = Field(ge=1)
    view_type: QrViewType = Field(default=QrViewType.text)
    image_width: int | None = Field(default=None, ge=1, le=4096)
    image_height: int | None = Field(default=None, ge=1, le=4096)
