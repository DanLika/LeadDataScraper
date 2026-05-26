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
        candidate = candidate[len(scheme):]

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


__all__ = [
    "DEFAULT_TIMESTAMP_TOLERANCE_SECONDS",
    "WebhookVerificationError",
    "MissingSignature",
    "BadSignature",
    "MissingTimestamp",
    "StaleTimestamp",
    "verify_hmac_sha256",
    "verify_timestamp_window",
]
