from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from struct import pack, unpack

_PAYLOAD_LEN = 10
_MAX_TTL_MIN = 0xFFFF
_SKEW_SEC = 60


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
    ttl_min = min(ttl_min, _MAX_TTL_MIN)
    nonce = secrets.token_bytes(4)
    payload = pack(">I H 4s", issued_at, ttl_min, nonce)
    sig = _sign(secret, payload)
    return f"{_b64url_encode(payload)}.{_b64url_encode(sig)}"


def _split_token(token: str) -> tuple[str, str] | None:
    try:
        return token.split(".", 1)
    except ValueError:
        return None


def _decode_token_parts(payload_b64: str, sig_b64: str) -> tuple[bytes, bytes] | None:
    try:
        payload = _b64url_decode(payload_b64)
        sig = _b64url_decode(sig_b64)
    except Exception:
        return None
    return payload, sig


def _parse_payload(payload: bytes) -> tuple[int, int, bytes] | None:
    if len(payload) != _PAYLOAD_LEN:
        return None
    try:
        issued_at, ttl_min, nonce = unpack(">I H 4s", payload)
    except Exception:
        return None
    return int(issued_at), int(ttl_min), nonce


def _is_time_valid(*, issued_at: int, expires_at: int, now_ts: int) -> bool:
    if expires_at < now_ts:
        return False
    # Small skew tolerance.
    return issued_at <= now_ts + _SKEW_SEC


def verify_master_invite_token(*, secret: str, token: str, now: int | None = None) -> MasterInviteClaims | None:
    parts = _split_token(token)
    if parts is None:
        return None
    payload_b64, sig_b64 = parts

    decoded = _decode_token_parts(payload_b64, sig_b64)
    if decoded is None:
        return None
    payload, sig = decoded

    expected = _sign(secret, payload)
    if not hmac.compare_digest(expected, sig):
        return None

    parsed = _parse_payload(payload)
    if parsed is None:
        return None
    issued_at, ttl_min, nonce_raw = parsed

    expires_at = int(issued_at) + int(ttl_min) * 60
    nonce = _b64url_encode(nonce_raw)

    now_ts = int(time.time()) if now is None else int(now)
    if not _is_time_valid(issued_at=issued_at, expires_at=expires_at, now_ts=now_ts):
        return None

    return MasterInviteClaims(issued_at=issued_at, expires_at=expires_at, nonce=nonce)
