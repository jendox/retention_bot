from __future__ import annotations

import re
import unittest

from src.security.master_invites import (
    create_master_invite_token,
    decode_master_invite_from_start,
    encode_master_invite_for_start,
    verify_master_invite_token,
)


class MasterInviteDeepLinkTests(unittest.TestCase):
    def test_encode_decode_roundtrip(self) -> None:
        secret = "test_secret"
        token = create_master_invite_token(secret=secret, ttl_sec=60)
        self.assertIn(".", token)

        encoded = encode_master_invite_for_start(token)
        self.assertNotIn(".", encoded)
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_-]+", encoded))

        decoded = decode_master_invite_from_start(encoded)
        self.assertEqual(decoded, token)

        claims = verify_master_invite_token(secret=secret, token=decoded)
        self.assertIsNotNone(claims)

    def test_decode_rejects_invalid_values(self) -> None:
        self.assertIsNone(decode_master_invite_from_start(""))
        self.assertIsNone(decode_master_invite_from_start("not_base64url!"))
