"""Dispatch policy — env-driven cold-outreach send tunables.

All values land here so a future operator can change them in Render env
without touching code. Defaults are deliberately conservative (research
recommendation for fresh Instantly subaccount): 30 emails/mailbox/day
once warmed; 10 during the 21-day warm-up window; business-hours-only
in the lead's local TZ.

Importing this module reads ``os.environ`` exactly once at process
boot; the snapshot is cached as a frozen ``DispatchPolicy`` instance.
Tests that need to override may call ``_reload_for_testing()`` — never
mutate ``os.environ`` mid-request (multi-worker uvicorn races; see the
canonical "AI-client constructors" rule in CLAUDE.md).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Literal

TimezoneMode = Literal["lead", "campaign", "UTC"]
_DEFAULT_SEND_DAYS = ("mon", "tue", "wed", "thu", "fri")
_VALID_DAY_TOKENS = frozenset({"mon", "tue", "wed", "thu", "fri", "sat", "sun"})


def _parse_send_days(raw: str) -> tuple[str, ...]:
    """``"mon,tue,wed,thu,fri"`` → ``("mon", "tue", "wed", "thu", "fri")``.

    Unknown tokens are silently dropped; an entirely-empty result falls
    back to the default. Operators who fat-finger the env get the safe
    default rather than a hard boot failure, but Mon-Fri stays the
    floor (no edge case turns Sunday on by accident).
    """
    tokens = [t.strip().lower() for t in (raw or "").split(",")]
    keep = tuple(t for t in tokens if t in _VALID_DAY_TOKENS)
    return keep or _DEFAULT_SEND_DAYS


def _parse_hhmm(raw: str, default: str) -> str:
    """``"09:00"`` → ``"09:00"`` (validated). Fallback on malformed input."""
    candidate = (raw or "").strip() or default
    parts = candidate.split(":")
    if len(parts) != 2:
        return default
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except ValueError:
        return default
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return default
    return f"{hh:02d}:{mm:02d}"


def _parse_tz_mode(raw: str) -> TimezoneMode:
    candidate = (raw or "").strip().lower()
    if candidate in ("lead", "campaign", "utc"):
        return "UTC" if candidate == "utc" else candidate  # type: ignore[return-value]
    return "lead"


@dataclass(frozen=True)
class DispatchPolicy:
    """Frozen snapshot of the dispatch tunables.

    Construct via :func:`load_dispatch_policy`; never instantiate
    directly outside tests.
    """

    daily_cap_per_mailbox: int = 30
    warmup_per_mailbox: int = 10
    send_window_start: str = "09:00"  # HH:MM, 24h
    send_window_end: str = "17:00"
    send_days: tuple[str, ...] = field(default_factory=lambda: _DEFAULT_SEND_DAYS)
    timezone_mode: TimezoneMode = "lead"

    def is_send_day(self, weekday_token: str) -> bool:
        """``weekday_token`` ∈ {mon..sun}. Caller computes day-of-week in
        the appropriate TZ first (see ``timezone_mode``)."""
        return weekday_token.lower() in self.send_days

    def is_in_window(self, hhmm: str) -> bool:
        """Lexicographic compare is safe on zero-padded HH:MM strings."""
        return self.send_window_start <= hhmm < self.send_window_end


def load_dispatch_policy(env: dict[str, str] | None = None) -> DispatchPolicy:
    """Build a policy from ``env`` (defaults to ``os.environ``).

    Each tunable has a permissive parse-with-fallback so an operator
    typo doesn't brick boot.
    """
    e = env if env is not None else os.environ
    return DispatchPolicy(
        daily_cap_per_mailbox=int(e.get("EMAIL_DAILY_CAP_PER_MAILBOX", "30") or "30"),
        warmup_per_mailbox=int(e.get("EMAIL_WARMUP_PER_MAILBOX", "10") or "10"),
        send_window_start=_parse_hhmm(e.get("SEND_WINDOW_START", ""), "09:00"),
        send_window_end=_parse_hhmm(e.get("SEND_WINDOW_END", ""), "17:00"),
        send_days=_parse_send_days(e.get("SEND_DAYS", "")),
        timezone_mode=_parse_tz_mode(e.get("SEND_TIMEZONE_MODE", "")),
    )


# Module-level snapshot — single read of os.environ at import time.
DISPATCH_POLICY: DispatchPolicy = load_dispatch_policy()


def _reload_for_testing(env: dict[str, str] | None = None) -> DispatchPolicy:
    """Test-only: rebuild the module snapshot from ``env``.

    Mutates the module global. Never call from request-handling code.
    """
    global DISPATCH_POLICY  # noqa: PLW0603 — module-state by design
    DISPATCH_POLICY = load_dispatch_policy(env)
    return DISPATCH_POLICY


__all__ = [
    "DispatchPolicy",
    "DISPATCH_POLICY",
    "TimezoneMode",
    "load_dispatch_policy",
    "replace",  # convenience re-export for callers that want a mutated copy
]
