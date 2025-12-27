from __future__ import annotations

import hmac
from collections.abc import Mapping, Sequence
from hashlib import sha1


def compute_signature(
    *,
    params: Mapping[str, str],
    secret_word: str,
    mapping: Sequence[str],
) -> str:
    """
    Express Pay: signature = HMAC-SHA1( concat(params in strict order), secret_word ), HEX upper.
    Параметры конкатенируются по заранее известному mapping-списку.
    """
    normalized = {k.lower(): (v or "") for k, v in params.items()}
    payload = "".join(normalized.get(field, "") for field in mapping)

    key = (secret_word or "").encode()
    msg = payload.encode()
    return hmac.new(key, msg, sha1).hexdigest().upper()


# Mapping-и для нужных тебе методов.
# (Список полей/порядок описаны в документации; ниже — только те методы, которые используем.)
SIGNATURE_MAPPING: dict[str, list[str]] = {
    "add-invoice": [
        "token",
        "accountno",
        "amount",
        "currency",
        "expiration",
        "info",
        "surname",
        "firstname",
        "patronymic",
        "city",
        "street",
        "house",
        "building",
        "apartment",
        "isnameeditable",
        "isaddresseditable",
        "isamounteditable",
        "emailnotification",
        "returninvoiceurl",
    ],
    "update-invoice": [
        "token",
        "invoiceid",
        "amount",
        "currency",
        "expiration",
        "info",
        "surname",
        "firstname",
        "patronymic",
        "city",
        "street",
        "house",
        "building",
        "apartment",
        "isnameeditable",
        "isaddresseditable",
        "isamounteditable",
        "emailnotification",
        "smsphone",
    ],
    # В документации встречается и invoiceno, и id для статуса/отмены.
    # Чтобы не упереться в несовпадение, мы будем в запрос класть ОБА поля
    # и подписывать по "id".
    "status-invoice": ["token", "id"],
    "cancel-invoice": ["token", "id"],
    "get-qr-code": ["token", "invoiceid", "viewtype", "imagewidth", "imageheight"],
}
