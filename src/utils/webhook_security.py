"""Webhook signature + timestamp verification.

Used by every provider webhook handler (Instantly today; Resend +
HeyReach next). The two primitives:

  * :func:`verify_hmac_sha256` — constant-time HMAC body verification.
    The provider's signing convention is shoved into the
    ``signature_scheme`` arg ("sha256=" prefix, "v1=" for Stripe-style,
    raw hex, etc.). Default mirrors Instantly + Resend ("sha256=...").

  * :func:`verify_timestamp_window` — bounded clock-skew gate that
    defends against replay attacks. Provider sends both the signature
    AND a timestamp header; valid HMAC + stale timestamp = replay.

Both raise :class:`WebhookVerificationError` on failure rather than
returning False so the handler can collapse every failure path to a
single 401 / 403 response — no leak of which check failed.

NEVER swap ``hmac.compare_digest`` for ``==``. Even a microsecond
side-channel leaks ~1 bit per probe to an attacker with enough request
budget. The standard library primitive is safe by construction.
"""

from __future__ import annotations

import base64
import hmac
import logging
import time
from hashlib import sha256
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMESTAMP_TOLERANCE_SECONDS = 300
"""Replay window. 5 minutes matches Stripe + GitHub conventions and is
generous enough for legitimate provider retry backoff."""


# ----- Errors ---------------------------------------------------------------


class WebhookVerificationError(Exception):
    """Base — handler should map to 401/403 with a generic body."""


class MissingSignature(WebhookVerificationError):
    """No signature header present at all."""


class BadSignature(WebhookVerificationError):
    """HMAC did not match — wrong secret OR tampered body."""


class MissingTimestamp(WebhookVerificationError):
    """No timestamp header present (provider should always send one)."""


class StaleTimestamp(WebhookVerificationError):
    """Timestamp is outside the tolerated window — likely replay."""


# ----- HMAC -----------------------------------------------------------------


def verify_hmac_sha256(
    payload: bytes,
    signature_header: str,
    secret: str,
    *,
    signature_scheme: str = "sha256=",
) -> None:
    """Raise :class:`WebhookVerificationError` if the signature does not match.

    Args:
        payload: Raw body bytes — the same byte sequence the provider
            HMAC'd. Do NOT re-encode through ``json.loads`` first;
            providers HMAC the literal bytes (whitespace + key order
            preserved) and a roundtrip changes either.
        signature_header: Value of the provider's signature header
            (e.g. ``X-Signature``). Scheme prefix (``sha256=...``) is
            stripped before compare. Empty string raises
            :class:`MissingSignature` so the caller can distinguish
            "no header" from "wrong header" if needed.
        secret: HMAC key — from env (``INSTANTLY_WEBHOOK_SIGNING_SECRET``
            or equivalent per provider). Empty raises ``RuntimeError`` —
            operator misconfig should fail loud, not silently accept
            every request.
        signature_scheme: Prefix to strip off the header value. Default
            ``"sha256="`` matches the canonical convention. Pass
            ``""`` (empty string) when the header is raw hex with no
            prefix (some providers like HeyReach do this).

    Returns:
        None on a clean match. Any failure raises a
        :class:`WebhookVerificationError` subclass.
    """
    if not secret:
        raise RuntimeError(
            "HMAC signing secret is empty — operator misconfigured "
            "the provider webhook env var"
        )
    if not signature_header:
        raise MissingSignature("signature header missing or empty")

    # Normalize the header value to lowercase BEFORE the prefix-strip
    # so a provider that uppercases the scheme tag ("SHA256=...") still
    # gets its prefix removed. Hex is case-insensitive; lowering keeps
    # compare_digest happy in both directions.
    candidate = signature_header.strip().lower()
    scheme = (signature_scheme or "").lower()
    if scheme and candidate.startswith(scheme):
        candidate = candidate[len(scheme) :]

    try:
        expected = hmac.new(secret.encode("utf-8"), payload, sha256).hexdigest()
    except TypeError as exc:
        # Misuse — e.g. payload was a str, not bytes. Treat as bad
        # signature rather than 500'ing the handler.
        raise BadSignature(f"HMAC compute failed: {exc!s}") from exc

    if not hmac.compare_digest(candidate, expected.lower()):
        raise BadSignature("HMAC signature mismatch")


# ----- Timestamp window -----------------------------------------------------


def verify_timestamp_window(
    timestamp_header: str,
    *,
    tolerance_seconds: int = DEFAULT_TIMESTAMP_TOLERANCE_SECONDS,
    now: Optional[int] = None,
) -> int:
    """Raise if ``timestamp_header`` is outside ±``tolerance_seconds``.

    Args:
        timestamp_header: Provider header value. Must parse to a unix
            epoch integer (seconds). Whitespace stripped.
        tolerance_seconds: Symmetric window — defaults to 300 (5min).
        now: Override for unit tests; production passes None and reads
            ``time.time()``.

    Returns:
        The parsed integer epoch on success (handy for the caller's logs).

    Raises:
        MissingTimestamp / StaleTimestamp.
    """
    if not timestamp_header:
        raise MissingTimestamp("timestamp header missing or empty")
    try:
        ts = int(timestamp_header.strip())
    except (ValueError, TypeError) as exc:
        raise MissingTimestamp(f"timestamp header not parseable: {exc!s}") from exc

    now_secs = int(now if now is not None else time.time())
    skew = abs(now_secs - ts)
    if skew > tolerance_seconds:
        raise StaleTimestamp(
            f"timestamp skew {skew}s exceeds tolerance {tolerance_seconds}s"
        )
    return ts


# ----- Svix scheme (Resend, ngrok, OpenAI Realtime, etc.) -------------------
#
# Svix is the webhook-delivery service Resend uses. Its signature scheme is
# distinct enough from the plain "sha256=<hex>" convention that the existing
# verify_hmac_sha256 cannot serve it:
#
#   * Headers: svix-id, svix-timestamp, svix-signature (3 separate headers
#     instead of 1 + 1).
#   * Payload-to-sign: "{svix_id}.{svix_timestamp}.{body}" (NOT the body
#     alone — the id + ts bind the signature to one specific delivery).
#   * Digest encoding: base64 (NOT hex).
#   * Header format: a SPACE-separated list of "v1,<base64-digest>" entries.
#     A single header may carry several versions in parallel — Svix uses
#     this to roll signing-key updates without breaking clients.
#   * Secret format: "whsec_<base64-secret-bytes>". The whsec_ prefix is
#     part of the textual representation; the HMAC key is the base64-
#     decoded suffix.
#
# Phase 16 TODO(resend-go-live): when Resend ingest is wired and
# Py3.10-built requirements.txt can be regenerated, replace this hand-roll
# with `from svix.webhooks import Webhook; wh.verify(payload, headers)`.
# The package handles future "v2,..." rotation we'd otherwise miss. Until
# then, this single-version verifier covers v1 (the only version Svix has
# emitted since 2020) and the test corpus exercises the rotation surface.
# ----------------------------------------------------------------------------


_SVIX_SUPPORTED_VERSIONS = frozenset({"v1"})


def verify_svix_signature(
    payload: bytes,
    svix_id: str,
    svix_timestamp: str,
    svix_signature_header: str,
    secret: str,
    *,
    tolerance_seconds: int = DEFAULT_TIMESTAMP_TOLERANCE_SECONDS,
    now: Optional[int] = None,
) -> None:
    """Verify a Svix-signed webhook (Resend, ngrok, etc.).

    Raises :class:`WebhookVerificationError` on any failure — the
    caller collapses every reject into one opaque 401 response so the
    attacker can't probe which check broke first.

    Args:
        payload: Raw body bytes — exactly as received. Re-serialising
            through json.loads + json.dumps changes byte-for-byte
            equality and breaks the HMAC.
        svix_id: Value of the ``svix-id`` request header.
        svix_timestamp: Value of the ``svix-timestamp`` request header
            (unix epoch seconds, as a string).
        svix_signature_header: Value of the ``svix-signature`` request
            header. May carry multiple space-separated versions
            (e.g. ``v1,abc... v1,def...``); ANY matching version
            passes.
        secret: HMAC key in the canonical ``whsec_<base64>`` form
            Resend / Svix issue. The ``whsec_`` prefix is part of the
            display format; the actual key is the base64-decoded
            suffix. Empty raises ``RuntimeError`` — operator misconfig
            must fail loud.
        tolerance_seconds: Replay window. Defaults to 300s (matches
            ``DEFAULT_TIMESTAMP_TOLERANCE_SECONDS`` for the Instantly
            handler — uniform behaviour across providers).
        now: Override for unit tests; production passes None.

    Notes:
        * No silent ``True`` return on a malformed header — we surface
          MissingSignature so the caller's structured log can pinpoint
          ingress drift to either "header missing" vs "HMAC mismatch".
        * ``hmac.compare_digest`` enforces constant-time compare per
          version — no early-exit on the first byte mismatch leaks
          which version was tried last.
    """
    if not secret:
        raise RuntimeError(
            "Svix signing secret is empty — operator misconfigured the "
            "RESEND_WEBHOOK_SIGNING_SECRET env var"
        )
    if not svix_id:
        raise MissingSignature("svix-id header missing or empty")
    if not svix_signature_header:
        raise MissingSignature("svix-signature header missing or empty")

    # Timestamp window first — a fresh signature on a stale timestamp
    # is still replay. Reusing the existing verify_timestamp_window
    # so the 300s tolerance + numeric-parse error semantics stay
    # uniform across providers.
    verify_timestamp_window(
        svix_timestamp,
        tolerance_seconds=tolerance_seconds,
        now=now,
    )

    # Decode the secret. The whsec_ prefix is textual decoration; the
    # actual key is whatever base64-decodes from the rest.
    if secret.startswith("whsec_"):
        secret_b64 = secret[len("whsec_") :]
    else:
        # Forward-compat: accept raw base64 without the prefix so
        # operators who set the env var without the human-readable
        # prefix don't get a silent reject.
        secret_b64 = secret
    try:
        secret_bytes = base64.b64decode(secret_b64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise RuntimeError(
            f"Svix signing secret is not valid base64: {exc!s}"
        ) from exc

    # Payload to sign: id.timestamp.body (three parts joined by '.').
    signed_payload = f"{svix_id}.{svix_timestamp}.".encode("utf-8") + payload
    expected_digest = base64.b64encode(
        hmac.new(secret_bytes, signed_payload, sha256).digest()
    ).decode("ascii")

    # Header is a space-separated list of "<version>,<base64-digest>".
    # ANY matching version passes — Svix uses this to ship a v1 + a
    # rotated-key v1 in parallel during a secret rotation.
    matched = False
    for entry in svix_signature_header.split(" "):
        entry = entry.strip()
        if not entry or "," not in entry:
            continue
        version, _, candidate = entry.partition(",")
        if version not in _SVIX_SUPPORTED_VERSIONS:
            # Forward-compat: ignore unknown versions rather than
            # failing the whole request. When Svix introduces v2, the
            # operator will still see v1 in the same header until they
            # roll; rejecting on v2-only would break that grace period.
            continue
        if hmac.compare_digest(candidate.strip(), expected_digest):
            matched = True
            break

    if not matched:
        raise BadSignature(
            "no matching v1 signature in svix-signature header"
        )


__all__ = [
    "DEFAULT_TIMESTAMP_TOLERANCE_SECONDS",
    "WebhookVerificationError",
    "MissingSignature",
    "BadSignature",
    "MissingTimestamp",
    "StaleTimestamp",
    "verify_hmac_sha256",
    "verify_svix_signature",
    "verify_timestamp_window",
]
