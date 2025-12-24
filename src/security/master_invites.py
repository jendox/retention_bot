from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from struct import pack, unpack


@dataclass(frozen=True)
class MasterInviteClaims:
    issued_at: int
    expires_at: int
    nonce: str


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _b64url_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("utf-8"))


def _sign(secret: str, payload: bytes) -> bytes:
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    # Keep start payload short: Telegram deep links have tight length limits.
    return digest[:12]


def encode_master_invite_for_start(token: str) -> str:
    """
    Telegram deep-link `start=` payload is limited and only allows [A-Za-z0-9_-].
    Our invite token contains '.', so we wrap it into base64url.
    """
    return _b64url_encode(token.encode("utf-8"))


def decode_master_invite_from_start(value: str) -> str | None:
    """
    Reverse `encode_master_invite_for_start`.
    Returns None if the value is not a valid encoded token.
    """
    try:
        decoded = _b64url_decode(value).decode("utf-8")
    except Exception:
        return None
    if "." not in decoded:
        return None
    return decoded


def create_master_invite_token(*, secret: str, ttl_sec: int) -> str:
    issued_at = int(time.time())
    ttl_min = max(1, int(ttl_sec) // 60)
    if ttl_min > 0xFFFF:
        ttl_min = 0xFFFF
    nonce = secrets.token_bytes(4)
    payload = pack(">I H 4s", issued_at, ttl_min, nonce)
    sig = _sign(secret, payload)
    return f"{_b64url_encode(payload)}.{_b64url_encode(sig)}"


def verify_master_invite_token(*, secret: str, token: str, now: int | None = None) -> MasterInviteClaims | None:
    try:
        payload_b64, sig_b64 = token.split(".", 1)
    except ValueError:
        return None

    try:
        payload = _b64url_decode(payload_b64)
        sig = _b64url_decode(sig_b64)
    except Exception:
        return None

    expected = _sign(secret, payload)
    if not hmac.compare_digest(expected, sig):
        return None

    if len(payload) != 10:
        return None

    try:
        issued_at, ttl_min, _nonce = unpack(">I H 4s", payload)
    except Exception:
        return None

    expires_at = int(issued_at) + int(ttl_min) * 60
    nonce = _b64url_encode(_nonce)

    now_ts = int(time.time()) if now is None else int(now)
    if expires_at < now_ts:
        return None
    # Small skew tolerance.
    if issued_at > now_ts + 60:
        return None

    return MasterInviteClaims(issued_at=issued_at, expires_at=expires_at, nonce=nonce)
