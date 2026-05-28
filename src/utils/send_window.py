"""Send-window resolver — checks whether ``now`` is inside a step's
configured send window in the lead's (or operator's) timezone.

The dispatch tick worker (``src/workers/dispatch_tick.py``) calls
:func:`is_within_window` on each claimed message before triggering the
dispatcher API. Out-of-window messages get returned to ``pending``
with ``scheduled_at`` bumped to the next valid window start so the
next tick picks them up at the right time.

Timezone resolution priority (Phase 15.2):

1. ``lead.timezone`` (column doesn't exist yet — deferred to Phase 19)
2. ``campaign.timezone`` (column also deferred)
3. ``SEND_WINDOW_DEFAULT_TZ`` env (operator's single-tenant default;
   default ``UTC`` if unset)

The current implementation only consults #3 — the wiring for #1 / #2
lands when the columns do. Single-tenant LDS doesn't need per-lead TZ
yet; one operator's window is the right window.

Uses :mod:`zoneinfo` (stdlib, Python 3.9+) so there is no third-party
TZ dependency. zoneinfo data comes from the system's IANA tz database;
on Render Linux this is always current.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Mon=0 .. Sun=6 → keyword token mapping. Mirrors the
# sequence_steps.send_days serialization format.
_WEEKDAY_TOKENS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class WindowCheck:
    """Outcome of an :func:`is_within_window` call.

    ``next_window_start_utc`` is None when ``in_window`` is True (no
    deferral needed); on False it points at the next UTC instant the
    message becomes eligible. Caller uses it to bump
    ``campaign_messages.scheduled_at`` and release the claim.
    """

    in_window: bool
    next_window_start_utc: Optional[datetime] = None


def is_within_window(
    *,
    step_send_window_start: str,
    step_send_window_end: str,
    step_send_days: str,
    timezone_name: Optional[str] = None,
    now_utc: Optional[datetime] = None,
) -> WindowCheck:
    """Check the (now_utc) instant against a step's send window.

    Args:
        step_send_window_start: ``HH:MM`` or ``HH:MM:SS`` string. PostgREST
            surfaces TIME columns in either form.
        step_send_window_end: Same shape. Inclusive of start, exclusive
            of end (matches the canonical
            ``DispatchPolicy.is_in_window`` semantics in
            ``src/utils/dispatch_policy.py``).
        step_send_days: Comma-separated weekday tokens
            (``"mon,tue,wed,thu,fri"``). Case-insensitive; unknown
            tokens silently dropped (matches dispatch_policy parse).
        timezone_name: IANA TZ string (``"Europe/Sarajevo"``,
            ``"America/New_York"``). When None or invalid, falls back to
            ``SEND_WINDOW_DEFAULT_TZ`` env, then ``"UTC"``.
        now_utc: Override for tests; production passes None and reads
            ``datetime.now(timezone.utc)``.

    Returns:
        :class:`WindowCheck`. On False, ``next_window_start_utc`` points
        at the next UTC instant the window opens; caller bumps
        ``scheduled_at`` to that value.
    """
    tz = _resolve_timezone(timezone_name)
    now_utc = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_local = now_utc.astimezone(tz)

    start_time = _parse_hhmm(step_send_window_start, fallback=time(9, 0))
    end_time = _parse_hhmm(step_send_window_end, fallback=time(17, 0))
    send_days = _parse_send_days(step_send_days)

    today_token = _WEEKDAY_TOKENS[now_local.weekday()]
    current_t = now_local.time()

    if today_token in send_days and start_time <= current_t < end_time:
        return WindowCheck(in_window=True)

    # Compute next valid window start.
    next_start_local = _next_window_start(
        now_local,
        send_days,
        start_time,
        end_time,
    )
    next_start_utc = next_start_local.astimezone(timezone.utc)
    return WindowCheck(in_window=False, next_window_start_utc=next_start_utc)


# ----- Internals -----------------------------------------------------------


def _resolve_timezone(name: Optional[str]) -> ZoneInfo:
    """Resolve to a :class:`ZoneInfo`. Falls back through:

    explicit → ``SEND_WINDOW_DEFAULT_TZ`` env → ``UTC``.
    """
    for candidate in (name, os.environ.get("SEND_WINDOW_DEFAULT_TZ"), "UTC"):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except ZoneInfoNotFoundError:
            continue
    # Final hard fallback — should never trigger unless the system has
    # no tzdata at all, which on Render Linux is impossible.
    return ZoneInfo("UTC")  # pragma: no cover


def _parse_hhmm(raw: str, *, fallback: time) -> time:
    """Accepts ``"09:00"`` or ``"09:00:00"`` (PostgREST TIME shape).

    Anything malformed → fallback. The DB column is TIME so the value
    is structurally valid in production; the parse-then-fallback path
    is for legacy / migration rows that might land in odd shapes.
    """
    if not isinstance(raw, str) or not raw:
        return fallback
    parts = raw.strip().split(":")
    if not (2 <= len(parts) <= 3):
        return fallback
    try:
        hh = int(parts[0])
        mm = int(parts[1])
        ss = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        return fallback
    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
        return fallback
    return time(hh, mm, ss)


def _parse_send_days(raw: str) -> frozenset[str]:
    """``"mon,tue,wed,thu,fri"`` → ``frozenset({"mon", "tue", ...})``.

    Case-insensitive. Unknown tokens silently dropped; entirely-empty
    result falls back to Mon-Fri (matches ``DispatchPolicy._parse_send_days``).
    """
    if not isinstance(raw, str):
        return frozenset(_WEEKDAY_TOKENS[:5])
    seen = {
        t.strip().lower()
        for t in raw.split(",")
        if t.strip().lower() in _WEEKDAY_TOKENS
    }
    return frozenset(seen) if seen else frozenset(_WEEKDAY_TOKENS[:5])


def _next_window_start(
    now_local: datetime,
    send_days: frozenset[str],
    start_time: time,
    end_time: time,
) -> datetime:
    """Compute the next datetime (in ``now_local`` tz) when the window opens.

    Algorithm: from today forward, find the first day whose weekday is in
    ``send_days``. If today qualifies AND we are before the window start,
    the answer is today at start_time. Otherwise, scan forward day-by-day
    (max 7 — every send_days config except impossible empty includes at
    least one weekday).
    """
    # Today, before start.
    today_token = _WEEKDAY_TOKENS[now_local.weekday()]
    if today_token in send_days and now_local.time() < start_time:
        return _at_time(now_local, start_time)
    # Future days. Step day-by-day; bounded loop in case of degenerate
    # input (defensive — DB CHECK + parser fallback already prevent
    # empty send_days reaching here).
    candidate = now_local
    for _ in range(8):  # up to a full week + 1 (covers edge cases)
        candidate += timedelta(days=1)
        token = _WEEKDAY_TOKENS[candidate.weekday()]
        if token in send_days:
            return _at_time(candidate, start_time)
    # Unreachable in practice (send_days is non-empty per parser).
    return _at_time(now_local + timedelta(days=1), start_time)  # pragma: no cover


def _at_time(dt: datetime, t: time) -> datetime:
    """Replace the time component of ``dt`` with ``t``, preserving TZ + date."""
    return datetime.combine(dt.date(), t, tzinfo=dt.tzinfo)


__all__ = ["WindowCheck", "is_within_window"]
