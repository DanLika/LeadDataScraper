"""Unit tests for src/utils/webhook_security.py.

HMAC + timestamp gates. Both raise the exact subclass of
WebhookVerificationError that pins the failure mode, so the handler
can collapse to a uniform 401 without leaking the cause.
"""
from __future__ import annotations

import hmac
import time
import unittest
from hashlib import sha256

from src.utils.webhook_security import (
    BadSignature,
    MissingSignature,
    MissingTimestamp,
    StaleTimestamp,
    verify_hmac_sha256,
    verify_timestamp_window,
)


SECRET = "test-webhook-signing-secret-32chars!!"
SAMPLE_PAYLOAD = b'{"event_id":"evt-1","event_type":"email_sent"}'


def _sign(payload: bytes, secret: str = SECRET, prefix: str = "sha256=") -> str:
    sig = hmac.new(secret.encode("utf-8"), payload, sha256).hexdigest()
    return f"{prefix}{sig}"


class TestHmacVerify(unittest.TestCase):
    def test_valid_signature_passes(self) -> None:
        verify_hmac_sha256(SAMPLE_PAYLOAD, _sign(SAMPLE_PAYLOAD), SECRET)

    def test_raw_hex_without_prefix(self) -> None:
        sig = hmac.new(SECRET.encode("utf-8"), SAMPLE_PAYLOAD, sha256).hexdigest()
        # Provider sends raw hex; caller passes empty scheme.
        verify_hmac_sha256(SAMPLE_PAYLOAD, sig, SECRET, signature_scheme="")

    def test_tampered_body_fails(self) -> None:
        sig = _sign(SAMPLE_PAYLOAD)
        tampered = SAMPLE_PAYLOAD + b"X"
        with self.assertRaises(BadSignature):
            verify_hmac_sha256(tampered, sig, SECRET)

    def test_wrong_secret_fails(self) -> None:
        sig = _sign(SAMPLE_PAYLOAD)
        with self.assertRaises(BadSignature):
            verify_hmac_sha256(SAMPLE_PAYLOAD, sig, "different-secret")

    def test_empty_signature_fails(self) -> None:
        with self.assertRaises(MissingSignature):
            verify_hmac_sha256(SAMPLE_PAYLOAD, "", SECRET)

    def test_empty_secret_raises_runtime(self) -> None:
        with self.assertRaises(RuntimeError):
            verify_hmac_sha256(SAMPLE_PAYLOAD, _sign(SAMPLE_PAYLOAD), "")

    def test_case_insensitive_hex_match(self) -> None:
        sig = _sign(SAMPLE_PAYLOAD).upper()
        # Some providers uppercase the hex; compare_digest is case-
        # sensitive but the wrapper normalises both sides.
        verify_hmac_sha256(SAMPLE_PAYLOAD, sig, SECRET)


class TestTimestampWindow(unittest.TestCase):
    def test_within_window_passes(self) -> None:
        now = int(time.time())
        result = verify_timestamp_window(str(now), now=now)
        self.assertEqual(result, now)

    def test_slight_drift_passes(self) -> None:
        now = int(time.time())
        verify_timestamp_window(str(now - 60), now=now)
        verify_timestamp_window(str(now + 60), now=now)

    def test_beyond_default_window_fails(self) -> None:
        now = int(time.time())
        with self.assertRaises(StaleTimestamp):
            verify_timestamp_window(str(now - 600), now=now)
        with self.assertRaises(StaleTimestamp):
            verify_timestamp_window(str(now + 600), now=now)

    def test_custom_tolerance(self) -> None:
        now = int(time.time())
        verify_timestamp_window(str(now - 30), tolerance_seconds=60, now=now)
        with self.assertRaises(StaleTimestamp):
            verify_timestamp_window(str(now - 90), tolerance_seconds=60, now=now)

    def test_empty_header_fails(self) -> None:
        with self.assertRaises(MissingTimestamp):
            verify_timestamp_window("")

    def test_garbage_header_fails(self) -> None:
        with self.assertRaises(MissingTimestamp):
            verify_timestamp_window("not-a-number")


if __name__ == "__main__":
    unittest.main()
