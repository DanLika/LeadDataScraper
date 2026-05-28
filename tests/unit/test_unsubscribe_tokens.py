"""Unit tests for src/utils/unsubscribe_tokens.py.

Round-trip + adversarial coverage:
- valid token round-trips through mint→verify
- tampered payload fails BadSignature
- expired token fails ExpiredToken
- malformed structure fails InvalidToken
- wrong secret fails BadSignature
- timestamp window: ±5 min OK on either side
- missing UNSUBSCRIBE_TOKEN_SECRET raises RuntimeError (operator misconfig)
"""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from src.utils.unsubscribe_tokens import (
    DEFAULT_TTL_DAYS,
    BadSignature,
    ExpiredToken,
    InvalidToken,
    UnsubscribePayload,
    mint,
    verify,
)


# Canonical UUID4 for testing (zero-pad version + variant bits don't
# matter for our string-shape check; only the canonical layout does).
SAMPLE_TRACKING_ID = "12345678-1234-1234-1234-123456789abc"
SAMPLE_SECRET = "test-secret-do-not-use-in-prod"


class TestRoundTrip(unittest.TestCase):
    def test_mint_then_verify_recovers_tracking_id(self) -> None:
        now = int(time.time())
        token = mint(SAMPLE_TRACKING_ID, secret=SAMPLE_SECRET, issued_at=now)
        result = verify(token, secret=SAMPLE_SECRET, now=now)
        self.assertIsInstance(result, UnsubscribePayload)
        self.assertEqual(result.tracking_id, SAMPLE_TRACKING_ID)
        self.assertEqual(result.issued_at, now)

    def test_token_is_urlsafe_base64(self) -> None:
        token = mint(SAMPLE_TRACKING_ID, secret=SAMPLE_SECRET)
        # urlsafe base64 alphabet: A-Z a-z 0-9 - _
        self.assertTrue(all(c.isalnum() or c in "-_" for c in token))
        # No padding (=) in the emitted token.
        self.assertNotIn("=", token)
        # Reasonable URL-length (well under 2000 chars).
        self.assertLess(len(token), 100)


class TestTampering(unittest.TestCase):
    def test_modified_payload_fails(self) -> None:
        token = mint(SAMPLE_TRACKING_ID, secret=SAMPLE_SECRET)
        # Flip one character in the middle of the payload section.
        # Token layout: v1 + 32-byte payload + 32-byte sig → ~88 chars base64.
        # Char 30 sits well inside the payload region.
        tampered = token[:30] + ("A" if token[30] != "A" else "B") + token[31:]
        with self.assertRaises((BadSignature, InvalidToken)):
            verify(tampered, secret=SAMPLE_SECRET)

    def test_truncated_token_fails(self) -> None:
        token = mint(SAMPLE_TRACKING_ID, secret=SAMPLE_SECRET)
        with self.assertRaises(InvalidToken):
            verify(token[:-10], secret=SAMPLE_SECRET)

    def test_empty_token_fails(self) -> None:
        with self.assertRaises(InvalidToken):
            verify("", secret=SAMPLE_SECRET)

    def test_garbage_base64_fails(self) -> None:
        with self.assertRaises(InvalidToken):
            verify("$%^!@", secret=SAMPLE_SECRET)


class TestWrongSecret(unittest.TestCase):
    def test_different_secret_fails(self) -> None:
        token = mint(SAMPLE_TRACKING_ID, secret=SAMPLE_SECRET)
        with self.assertRaises(BadSignature):
            verify(token, secret="completely-different-secret")


class TestExpiry(unittest.TestCase):
    def test_age_within_ttl_passes(self) -> None:
        long_ago = (
            int(time.time()) - DEFAULT_TTL_DAYS * 86_400 + 3600
        )  # 1 hour shy of expiry
        token = mint(SAMPLE_TRACKING_ID, secret=SAMPLE_SECRET, issued_at=long_ago)
        result = verify(token, secret=SAMPLE_SECRET)
        self.assertEqual(result.tracking_id, SAMPLE_TRACKING_ID)

    def test_age_beyond_ttl_fails(self) -> None:
        too_old = int(time.time()) - DEFAULT_TTL_DAYS * 86_400 - 1
        token = mint(SAMPLE_TRACKING_ID, secret=SAMPLE_SECRET, issued_at=too_old)
        with self.assertRaises(ExpiredToken):
            verify(token, secret=SAMPLE_SECRET)

    def test_custom_ttl_respected(self) -> None:
        twenty_min_old = int(time.time()) - 1200
        token = mint(SAMPLE_TRACKING_ID, secret=SAMPLE_SECRET, issued_at=twenty_min_old)
        # 10-minute TTL — 20-min-old token expires.
        with self.assertRaises(ExpiredToken):
            verify(token, secret=SAMPLE_SECRET, ttl_days=0)  # 0 days = 0s TTL

    def test_modest_clock_skew_tolerated(self) -> None:
        # Token issued 60 seconds in the future (clock skew between
        # mail relay + recipient + LDS host).
        future = int(time.time()) + 60
        token = mint(SAMPLE_TRACKING_ID, secret=SAMPLE_SECRET, issued_at=future)
        # Verify with current time — should pass via the 5-min skew window.
        result = verify(token, secret=SAMPLE_SECRET, now=int(time.time()))
        self.assertEqual(result.tracking_id, SAMPLE_TRACKING_ID)

    def test_excessive_future_timestamp_fails(self) -> None:
        # 10 minutes in the future — beyond the 5-min skew tolerance.
        too_far_future = int(time.time()) + 600
        token = mint(SAMPLE_TRACKING_ID, secret=SAMPLE_SECRET, issued_at=too_far_future)
        with self.assertRaises(InvalidToken):
            verify(token, secret=SAMPLE_SECRET, now=int(time.time()))


class TestTrackingIdShape(unittest.TestCase):
    def test_non_uuid_rejected_at_mint(self) -> None:
        with self.assertRaises(ValueError):
            mint("not-a-uuid", secret=SAMPLE_SECRET)
        with self.assertRaises(ValueError):
            mint("123456781234123412341234567812345", secret=SAMPLE_SECRET)  # no dashes
        with self.assertRaises(ValueError):
            mint("12345678-1234-1234-1234-12345678", secret=SAMPLE_SECRET)  # too short


class TestSecretResolution(unittest.TestCase):
    def test_missing_env_secret_raises(self) -> None:
        # Both mint and verify require a secret. Explicit empty env →
        # RuntimeError so operator misconfig fails loud at the call site.
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                mint(SAMPLE_TRACKING_ID)
            with self.assertRaises(RuntimeError):
                verify("anything", secret=None)

    def test_env_secret_picked_up_when_param_omitted(self) -> None:
        with patch.dict("os.environ", {"UNSUBSCRIBE_TOKEN_SECRET": "env-secret"}):
            token = mint(SAMPLE_TRACKING_ID)  # uses env
            # Verify with explicit secret should match.
            result = verify(token, secret="env-secret")
            self.assertEqual(result.tracking_id, SAMPLE_TRACKING_ID)


if __name__ == "__main__":
    unittest.main()
