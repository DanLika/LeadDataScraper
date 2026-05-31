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

import base64

from src.utils.webhook_security import (
    BadSignature,
    MissingSignature,
    MissingTimestamp,
    StaleTimestamp,
    verify_hmac_sha256,
    verify_svix_signature,
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


# ---------------------------------------------------------------------------
# Phase 16 (T2) — Svix signature verifier
# ---------------------------------------------------------------------------


# Raw secret bytes that get base64'd into the whsec_ display form.
_SVIX_RAW_SECRET = b"phase16-svix-test-key-bytes-32xx"
_SVIX_SECRET = "whsec_" + base64.b64encode(_SVIX_RAW_SECRET).decode("ascii")


def _svix_sign(
    svix_id: str,
    svix_timestamp: str,
    body: bytes,
    secret_bytes: bytes = _SVIX_RAW_SECRET,
    *,
    version: str = "v1",
) -> str:
    """Produce a valid svix-signature header value for the given inputs.

    The Svix scheme HMACs `f"{id}.{ts}.{body}"` with the base64-decoded
    secret and base64-encodes the digest. Header format is
    `<version>,<base64-digest>`. Multiple space-separated entries are
    legal (for key rotation).
    """
    msg = f"{svix_id}.{svix_timestamp}.".encode("utf-8") + body
    digest = base64.b64encode(hmac.new(secret_bytes, msg, sha256).digest()).decode("ascii")
    return f"{version},{digest}"


class TestSvixSignature(unittest.TestCase):
    def test_valid_signature_accepts(self) -> None:
        body = b'{"type": "email.delivered", "data": {"id": "abc"}}'
        ts = str(int(time.time()))
        sig = _svix_sign("msg_001", ts, body)
        # Should not raise.
        verify_svix_signature(body, "msg_001", ts, sig, _SVIX_SECRET)

    def test_tampered_body_rejected(self) -> None:
        body = b'{"type": "email.delivered"}'
        ts = str(int(time.time()))
        sig = _svix_sign("msg_002", ts, body)
        tampered = b'{"type": "email.replied"}'  # different bytes
        with self.assertRaises(BadSignature):
            verify_svix_signature(tampered, "msg_002", ts, sig, _SVIX_SECRET)

    def test_wrong_id_rejected(self) -> None:
        body = b'{}'
        ts = str(int(time.time()))
        sig = _svix_sign("msg_003", ts, body)
        with self.assertRaises(BadSignature):
            verify_svix_signature(body, "msg_004_wrong", ts, sig, _SVIX_SECRET)

    def test_wrong_timestamp_rejected(self) -> None:
        body = b'{}'
        ts = str(int(time.time()))
        sig = _svix_sign("msg_005", ts, body)
        wrong_ts = str(int(ts) + 1)
        # The timestamp window still passes (1s skew), but the HMAC
        # signed the original ts so it no longer matches.
        with self.assertRaises(BadSignature):
            verify_svix_signature(body, "msg_005", wrong_ts, sig, _SVIX_SECRET)

    def test_stale_timestamp_rejected(self) -> None:
        body = b'{}'
        ts = str(int(time.time()) - 3600)  # 1h old
        sig = _svix_sign("msg_006", ts, body)
        with self.assertRaises(StaleTimestamp):
            verify_svix_signature(body, "msg_006", ts, sig, _SVIX_SECRET)

    def test_missing_id_rejected(self) -> None:
        body = b'{}'
        ts = str(int(time.time()))
        sig = _svix_sign("msg_007", ts, body)
        with self.assertRaises(MissingSignature):
            verify_svix_signature(body, "", ts, sig, _SVIX_SECRET)

    def test_missing_signature_rejected(self) -> None:
        body = b'{}'
        ts = str(int(time.time()))
        with self.assertRaises(MissingSignature):
            verify_svix_signature(body, "msg_008", ts, "", _SVIX_SECRET)

    def test_empty_secret_raises_runtime(self) -> None:
        body = b'{}'
        ts = str(int(time.time()))
        sig = _svix_sign("msg_009", ts, body)
        with self.assertRaises(RuntimeError):
            verify_svix_signature(body, "msg_009", ts, sig, "")

    def test_secret_without_prefix_accepted(self) -> None:
        """Forward-compat: operators sometimes drop the whsec_ prefix.
        The verifier accepts raw base64 in that case so the env-var
        gets a graceful path either way."""
        body = b'{}'
        ts = str(int(time.time()))
        sig = _svix_sign("msg_010", ts, body)
        raw_b64 = base64.b64encode(_SVIX_RAW_SECRET).decode("ascii")
        verify_svix_signature(body, "msg_010", ts, sig, raw_b64)

    def test_multi_version_header_any_match(self) -> None:
        """Svix rotates secrets by emitting both old + new signatures
        in the same header. We must accept on ANY match."""
        body = b'{}'
        ts = str(int(time.time()))
        wrong = _svix_sign("msg_011", ts, body, b"wrong-secret-bytes-padding-xyzab")
        right = _svix_sign("msg_011", ts, body)
        composite = f"{wrong} {right}"
        verify_svix_signature(body, "msg_011", ts, composite, _SVIX_SECRET)

    def test_unknown_version_ignored(self) -> None:
        """Forward-compat: a future v2 we don't recognise must not
        fail the request when a valid v1 is also present."""
        body = b'{}'
        ts = str(int(time.time()))
        good_v1 = _svix_sign("msg_012", ts, body)
        unknown_v2 = "v2,unknown-future-format-bytes-here"
        composite = f"{unknown_v2} {good_v1}"
        verify_svix_signature(body, "msg_012", ts, composite, _SVIX_SECRET)

    def test_only_unknown_versions_rejected(self) -> None:
        body = b'{}'
        ts = str(int(time.time()))
        with self.assertRaises(BadSignature):
            verify_svix_signature(
                body, "msg_013", ts, "v2,abc v3,def", _SVIX_SECRET,
            )

    def test_invalid_base64_secret_raises_runtime(self) -> None:
        body = b'{}'
        ts = str(int(time.time()))
        sig = _svix_sign("msg_014", ts, body)
        with self.assertRaises(RuntimeError):
            verify_svix_signature(body, "msg_014", ts, sig, "whsec_!!!not-base64!!!")


if __name__ == "__main__":
    unittest.main()
