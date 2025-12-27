from __future__ import annotations

from typing import Any, Self

import httpx
from pydantic import TypeAdapter, ValidationError

from .exceptions import (
    ExpressPayApiError,
    ExpressPayErrorPayload,
    ExpressPayTransportError,
)
from .models.common import Envelope
from .models.invoice import (
    CreateInvoiceInput,
    CreateInvoiceResponse,
    InvoiceStatus,
    InvoiceStatusResponse,
    UpdateInvoicePatch,
)
from .models.qrcode import QrCodeRequest, QrCodeResponse
from .settings import ExpressPaySettings
from .signature import SIGNATURE_MAPPING, compute_signature
from .utils import default_epos_account_no, format_amount, format_expiration


class ExpressPayClient:

    def __init__(self, settings: ExpressPaySettings, *, http: httpx.AsyncClient | None = None):
        self._settings = settings
        self._external_http = http is not None
        self._http = http or httpx.AsyncClient(
            base_url=self._settings.api_base_url,
            timeout=self._settings.timeout,
        )

    async def aclose(self) -> None:
        if not self._external_http:
            await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    # -----------------------------
    # Public API
    # -----------------------------

    async def create_invoice(self, data: CreateInvoiceInput) -> tuple[int, str | None]:
        """
        Выход: (invoice_no, invoice_url).
        """
        account_no = default_epos_account_no(data.master_id, base_account_number=self._settings.account_number)

        params: dict[str, str] = {
            "Token": self._settings.token,
            "AccountNo": account_no,
            "Amount": format_amount(data.amount),
            "Currency": str(int(data.currency)),
            "Info": data.description,
            "ReturnInvoiceUrl": "1",
        }

        # Expiration / LifeTime
        if data.expires_at is not None:
            params["Expiration"] = format_expiration(data.expires_at)
        elif data.lifetime_seconds is not None:
            params["LifeTime"] = str(int(data.lifetime_seconds))
            # В mapping для signature используется expiration —
            # просто не кладём Expiration, а signature builder подставит "".

        self._maybe_sign(params, action="add-invoice")

        payload = await self._request_json(
            method="POST",
            url="/v1/invoices",
            query={"token": self._settings.token},
            data=params,
        )

        try:
            response = TypeAdapter(CreateInvoiceResponse).validate_python(payload)
        except ValidationError as exc:
            if isinstance(payload, dict) and isinstance(payload.get("Message"), str):
                raise ExpressPayApiError(
                    ExpressPayErrorPayload(code=401, msg=str(payload["Message"]), msg_code=0),
                    raw=payload,
                ) from exc
            raise ExpressPayTransportError("Unexpected ExpressPay response schema for create_invoice") from exc
        return response.InvoiceNo, response.InvoiceUrl

    async def update_invoice(self, invoice_no: int, patch: UpdateInvoicePatch) -> bool:
        """
        Возвращает True, если запрос успешен (ошибки кидаем исключением).
        """
        params: dict[str, str] = {
            "Token": self._settings.token,
            "InvoiceId": str(int(invoice_no)),
        }

        if patch.amount is not None:
            params["Amount"] = format_amount(patch.amount)
        if patch.currency is not None:
            params["Currency"] = str(int(patch.currency))
        if patch.description is not None:
            params["Info"] = patch.description
        if patch.expires_at is not None:
            params["Expiration"] = format_expiration(patch.expires_at)

        self._maybe_sign(params, action="update-invoice")

        await self._request_json(
            method="PUT",
            url="/v1/invoices",
            query={"token": self._settings.token},
            data=params,
        )
        return True

    async def cancel_invoice(self, invoice_no: int) -> bool:
        """
        DELETE /v1/invoices/{InvoiceNo}?token=...
        Возвращает True при успехе.
        """
        # Чтобы не зависеть от разночтений invoiceno vs id в подписи — кладём оба.
        query: dict[str, str] = {
            "token": self._settings.token,
            "Token": self._settings.token,
            "InvoiceNo": str(int(invoice_no)),
            "id": str(int(invoice_no)),
        }
        if self._settings.use_signature:
            signature = compute_signature(
                params=query,
                secret_word=self._settings.secret_word,
                mapping=SIGNATURE_MAPPING["cancel-invoice"],
            )
            query["signature"] = signature

        await self._request_json(method="DELETE", url=f"/v1/invoices/{int(invoice_no)}", query=query)
        return True

    async def get_invoice_status(self, invoice_no: int) -> InvoiceStatus:
        query: dict[str, str] = {
            "token": self._settings.token,
            "Token": self._settings.token,
            "InvoiceNo": str(int(invoice_no)),
            "id": str(int(invoice_no)),
        }
        if self._settings.use_signature:
            signature = compute_signature(
                params=query,
                secret_word=self._settings.secret_word,
                mapping=SIGNATURE_MAPPING["status-invoice"],
            )
            query["signature"] = signature

        payload = await self._request_json(method="GET", url=f"/v1/invoices/{int(invoice_no)}/status", query=query)
        response = TypeAdapter(InvoiceStatusResponse).validate_python(payload)
        return response.Status

    async def get_invoice_qrcode(self, request: QrCodeRequest) -> str:
        """
        Возвращает либо ссылку (view_type=text), либо base64 (view_type=base64).
        """
        query: dict[str, str] = {
            "token": self._settings.token,
            "Token": self._settings.token,
            "InvoiceId": str(int(request.invoice_id)),
            "ViewType": request.view_type.value,
        }
        if request.image_width is not None:
            query["ImageWidth"] = str(int(request.image_width))
        if request.image_height is not None:
            query["ImageHeight"] = str(int(request.image_height))

        if self._settings.use_signature:
            signature = compute_signature(
                params=query,
                secret_word=self._settings.secret_word,
                mapping=SIGNATURE_MAPPING["get-qr-code"],
            )
            query["signature"] = signature

        payload = await self._request_json(method="GET", url="/v1/qrcode/getqrcode/", query=query)
        response = TypeAdapter(QrCodeResponse).validate_python(payload)
        return response.QrCodeBody

    # -----------------------------
    # Internals
    # -----------------------------

    def _maybe_sign(self, params: dict[str, str], *, action: str) -> None:
        if not self._settings.use_signature:
            return
        mapping = SIGNATURE_MAPPING[action]
        signature = compute_signature(params=params, secret_word=self._settings.secret_word, mapping=mapping)
        params["signature"] = signature

    async def _request_json(
        self,
        *,
        method: str,
        url: str,
        query: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> Any:
        try:
            response = await self._http.request(method, url, params=query, data=data)
        except httpx.HTTPError as e:
            raise ExpressPayTransportError(f"HTTP transport error: {e!r}") from e

        # ExpressPay отвечает JSON’ом в любом случае (успех/ошибка).
        try:
            payload = response.json()
        except ValueError as e:
            raise ExpressPayTransportError(f"Non-JSON response, status={response.status_code}") from e

        env = TypeAdapter(Envelope).validate_python(payload)
        if env.Error is not None:
            raise ExpressPayApiError(
                ExpressPayErrorPayload(
                    code=env.Error.Code,
                    msg=env.Error.Msg,
                    msg_code=env.Error.MsgCode,
                ),
                raw=payload,
            )

        return payload
