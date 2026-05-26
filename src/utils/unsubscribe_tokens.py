"""RFC 8058 List-Unsubscribe-Post token mint + verify.

Gmail (2024-02) and Yahoo (same date) + Microsoft (2025-04) require:

  1. ``List-Unsubscribe: <https://...>, <mailto:...>``  header
  2. ``List-Unsubscribe-Post: List-Unsubscribe=One-Click``  header
  3. Recipient receives an HTTP POST (no body, no consent flow) — must
     unsubscribe the sender from the recipient's perspective.

This module mints opaque base64url tokens carrying
``(tracking_id, campaign_id, issued_at, exp)`` — the unsubscribe handler
verifies the HMAC, the timestamp window, and dereferences
``tracking_id`` against ``campaign_messages`` to find the
``(lead_unique_key, channel)`` tuple to insert into ``suppressions``.

Why HMAC, not a JWT lib:
- One signing operation, one verification — JWT lib overhead unjustified.
- Avoid JOSE alg=none confusion + lib CVE blast radius.
- The token is opaque to recipients; no need for JSON readability.

Why timestamp window:
- Mailbox providers cache email content; a recipient may click a 90-day-
  old unsubscribe link. We accept ≤ ``DEFAULT_TTL_DAYS`` (90) days from
  ``issued_at``. Older tokens 410-Gone (operator can re-send with a
  fresh List-Unsubscribe).
- Allows scoped revocation if the signing secret is rotated (next env
  ``UNSUBSCRIBE_TOKEN_SECRET_V2`` flow lands when needed).

Why ``hmac.compare_digest``:
- Timing-safe — drops the test-vector + brute-force attack ladder.
"""
from __future__ import annotations

import base64
import hmac
import os
import struct
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Optional

# ----- Constants ------------------------------------------------------------

DEFAULT_TTL_DAYS = 90
"""Recipient mail clients cache emails; a 90-day window matches the
Mailgun + Mailmodo recommendation for one-click unsubscribe."""

_TOKEN_VERSION = b"v1"
"""Single-byte version prefix — lets a future signing-scheme migration
reject old-version tokens (raises ``InvalidToken``) without breaking
in-flight legitimate ones."""

_SIGNATURE_BYTES = 32  # HMAC-SHA256 = 32 raw bytes
_PAYLOAD_FORMAT = ">16sIQI"  # version, big-endian: tracking_id_bytes(16) | issued_at(u32 epoch_secs) | tracking_id_check(u64) | reserved(u32)
# Above format yields a 32-byte payload (16 + 4 + 8 + 4 = 32). The
# tracking_id_check field doubles as a sanity tag for v1 (zeros allowed).
# Together with the 2-byte version prefix and 32-byte signature: total
# 66 bytes raw → 88-char base64url string (well under URL length limits).


# ----- Public errors --------------------------------------------------------


class TokenError(Exception):
    """Base class. Handlers should map all subclasses to 410-Gone."""


class InvalidToken(TokenError):
    """Malformed structure / wrong version / corrupt base64."""


class BadSignature(TokenError):
    """HMAC verification failed. Most likely tampered or wrong secret."""


class ExpiredToken(TokenError):
    """Issued_at + TTL is in the past."""


# ----- Payload dataclass ----------------------------------------------------


@dataclass(frozen=True)
class UnsubscribePayload:
    """Decoded contents of a verified token."""

    tracking_id: str  # 36-char canonical UUID
    issued_at: int  # epoch seconds


# ----- Mint -----------------------------------------------------------------


def mint(tracking_id: str, *, secret: Optional[str] = None,
         issued_at: Optional[int] = None) -> str:
    """Return a URL-safe base64 token binding ``tracking_id`` to ``issued_at``.

    ``tracking_id`` must be a canonical UUID-shaped string (36 chars,
    ``xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx``); other shapes raise
    ``ValueError``. The handler's tracking_id is the
    ``campaign_messages.tracking_id`` UUID minted by the DB default.

    ``secret`` defaults to ``UNSUBSCRIBE_TOKEN_SECRET`` env var. The
    operator MUST set this — falling back to a hardcoded constant would
    let any leaked-token holder forge any other token.
    """
    if not _looks_like_uuid(tracking_id):
        raise ValueError(f"tracking_id must be a canonical UUID; got {tracking_id!r}")
    key = _resolve_secret(secret)
    now = int(issued_at if issued_at is not None else time.time())
    payload = _pack_payload(tracking_id, now)
    sig = hmac.new(key, _TOKEN_VERSION + payload, sha256).digest()
    raw = _TOKEN_VERSION + payload + sig
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


# ----- Verify ---------------------------------------------------------------


def verify(token: str, *, secret: Optional[str] = None,
           ttl_days: int = DEFAULT_TTL_DAYS,
           now: Optional[int] = None) -> UnsubscribePayload:
    """Round-trip verify a token. Raises a ``TokenError`` subclass on any failure.

    Argument layout intentionally requires ``ttl_days`` and ``now`` as
    keyword-only so the test surface is explicit about clock control.
    """
    if not token or not isinstance(token, str):
        raise InvalidToken("empty token")
    key = _resolve_secret(secret)
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, TypeError) as exc:
        raise InvalidToken(f"base64 decode failed: {exc!s}") from exc

    # 2 (version) + 32 (payload) + 32 (signature) = 66 bytes
    if len(raw) != 2 + 32 + _SIGNATURE_BYTES:
        raise InvalidToken(f"token length {len(raw)} != expected 66")
    if raw[:2] != _TOKEN_VERSION:
        raise InvalidToken(f"unknown token version {raw[:2]!r}")

    payload, signature = raw[2:2 + 32], raw[2 + 32:]
    expected = hmac.new(key, _TOKEN_VERSION + payload, sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise BadSignature("HMAC verification failed")

    tracking_id, issued_at = _unpack_payload(payload)

    now_secs = int(now if now is not None else time.time())
    age_secs = now_secs - issued_at
    if age_secs < 0:
        # Clock skew tolerance — accept up to 5 minutes in the future
        # (recipient mail client + relay timestamp drift).
        if age_secs < -300:
            raise InvalidToken(f"issued_at {issued_at} is in the future")
    elif age_secs > ttl_days * 86_400:
        raise ExpiredToken(
            f"token age {age_secs}s exceeds TTL {ttl_days}d"
        )

    return UnsubscribePayload(tracking_id=tracking_id, issued_at=issued_at)


# Backend handler path: `backend/main.py` exposes
# GET/POST /unsubscribe/{token}. Producer code that builds outbound URLs
# MUST use this constant so the dispatcher and handler can never drift on
# the path segment.
UNSUBSCRIBE_URL_PATH_SEGMENT = "unsubscribe"


def build_unsubscribe_url(base_url: str, tracking_id: str) -> str:
    """Return the fully-qualified per-message unsubscribe URL.

    Single source of truth for the producer side. Internally calls
    :func:`mint` so the returned URL terminates in an HMAC-signed token
    the handler will accept; the path segment matches
    :data:`UNSUBSCRIBE_URL_PATH_SEGMENT` so any future handler-path
    rename has exactly one matching producer to update.

    Args:
        base_url: Scheme + host (with or without trailing slash).
        tracking_id: ``campaign_messages.tracking_id`` UUID.

    Returns:
        URL of the shape ``<base>/unsubscribe/<token>`` ready to embed
        in email body / List-Unsubscribe header.

    Raises:
        ValueError: ``tracking_id`` is not UUID-shaped (propagated from
            :func:`mint`).
        RuntimeError: ``UNSUBSCRIBE_TOKEN_SECRET`` env unset.
    """
    base = base_url.rstrip("/")
    token = mint(tracking_id)
    return f"{base}/{UNSUBSCRIBE_URL_PATH_SEGMENT}/{token}"


# ----- Internals ------------------------------------------------------------


def _resolve_secret(secret: Optional[str]) -> bytes:
    """Resolve the HMAC key. Empty / unset secret is a hard failure —
    falling back to a hardcoded constant would defeat the whole scheme.
    """
    key = secret if secret is not None else os.environ.get("UNSUBSCRIBE_TOKEN_SECRET", "")
    if not key:
        raise RuntimeError(
            "UNSUBSCRIBE_TOKEN_SECRET is not configured — cannot mint or verify"
        )
    return key.encode("utf-8")


def _pack_payload(tracking_id: str, issued_at: int) -> bytes:
    """Pack (tracking_id, issued_at) into 32 bytes.

    UUID4 is 16 raw bytes. ``issued_at`` is a u32 epoch (good through
    2106-02-07; revisit in Phase 100). The trailing 12 bytes are reserved
    for a future schema-bump without re-issuing fresh tokens.
    """
    raw_uuid = _uuid_bytes(tracking_id)
    return raw_uuid + struct.pack(">I", issued_at) + b"\x00" * 12


def _unpack_payload(payload: bytes) -> tuple[str, int]:
    if len(payload) != 32:
        raise InvalidToken(f"payload length {len(payload)} != 32")
    raw_uuid = payload[:16]
    issued_at = struct.unpack(">I", payload[16:20])[0]
    tracking_id = _bytes_to_uuid(raw_uuid)
    return tracking_id, issued_at


def _looks_like_uuid(s: str) -> bool:
    if not isinstance(s, str) or len(s) != 36:
        return False
    parts = s.split("-")
    if len(parts) != 5 or [len(p) for p in parts] != [8, 4, 4, 4, 12]:
        return False
    return all(c in "0123456789abcdefABCDEF" for p in parts for c in p)


def _uuid_bytes(s: str) -> bytes:
    return bytes.fromhex(s.replace("-", ""))


def _bytes_to_uuid(b: bytes) -> str:
    h = b.hex()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


__all__ = [
    "DEFAULT_TTL_DAYS",
    "UNSUBSCRIBE_URL_PATH_SEGMENT",
    "UnsubscribePayload",
    "TokenError",
    "InvalidToken",
    "BadSignature",
    "ExpiredToken",
    "mint",
    "verify",
    "build_unsubscribe_url",
]
