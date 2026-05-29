"""ISO-8601 timestamp parsing tolerant to non-3/6-digit microseconds.

Python 3.10's ``datetime.fromisoformat`` rejects timestamps whose microsecond
component has a length other than 3 or 6 digits, raising
``ValueError: Invalid isoformat string: ...`` on inputs like
``"2026-05-28T14:45:34.51428+00:00"`` (5 digits). Python 3.11+ accepts
arbitrary fractional precision via PEP-654 changes, but the production
container is Python 3.10 (Microsoft Playwright base image
``v1.60.0-jammy``) while CI runs Python 3.12 — so the bug surfaces only
at cold-start in production and is invisible to the local test suite.

Supabase / PostgREST emits ``timestamptz`` values with whatever subsecond
precision the underlying ``CURRENT_TIMESTAMP`` capture produced, which
ranges 3–6 digits in practice and is not guaranteed to be exactly 6.

``parse_iso_timestamp`` delegates to ``dateutil.parser.isoparse`` which
handles arbitrary microsecond width on every supported Python release.
``python-dateutil`` is already a direct dependency (``requirements.in``).
"""

from __future__ import annotations

from datetime import datetime

from dateutil.parser import isoparse


def parse_iso_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 timestamp tolerant to 1–9 digit fractional seconds.

    Accepts both ``Z`` and ``+00:00`` UTC indicators (``isoparse`` handles
    both natively, so the legacy ``.replace("Z", "+00:00")`` shim used at
    every call site is no longer required).

    Raises ``ValueError`` on truly malformed input (same contract as
    ``datetime.fromisoformat``) so callers keep their existing
    ``try/except ValueError`` semantics.
    """
    return isoparse(value)
