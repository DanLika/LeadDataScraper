"""SQLite-backed daily Gemini token-budget circuit breaker.

Why a separate budget on top of slowapi
---------------------------------------
slowapi caps the *rate* of authed calls (e.g. ``/ask`` at 10/min,
``/process-all`` at 3/min) but a determined caller — or a compromised
API key — can still sustain calls 24h × 60 minutes and rack up real
Gemini spend.  The defense documented in CLAUDE.md
("AI and destructive endpoints capped via slowapi") was assumed to
also bound cost; it does NOT (rate × full day × per-call token cost
= unbounded).  This module adds a hard ceiling on the *total tokens*
spent across all Gemini calls in a UTC day so the worst-case bill
is bounded by ``GEMINI_DAILY_TOKEN_CEILING``.

Why SQLite, not Redis
---------------------
No new external dependency, no extra deploy surface, and the budget
counter is small enough that a single-writer SQLite file in WAL mode
handles every uvicorn worker we ship.  Render's filesystem persists
the file between deploys (image-mounted volume); a worker crash
leaves the row intact.  If we ever shard across multiple boxes,
swap the backend (the public API is module-level and stable).

Why daily, not per-minute
-------------------------
Gemini's billing windows are daily; the operator's monthly bill
correlates with daily token totals, not minute-by-minute rates.
slowapi already covers the burst direction.  A daily counter also
keeps state cheap (one row per UTC day) and the reset semantics
intuitive ("the ceiling resets at midnight UTC").

Threadsafety
------------
SQLite + WAL allows concurrent readers and one writer.  The
critical section here is *check + write*:  two workers can race
the read-modify-write of today's row and both pass the ceiling
check before either increments, then both increment past it.
A module-level ``_LOCK`` (``threading.Lock``) serializes
``check_budget`` and ``record_usage`` so the read+write happens
atomically.  Each connection uses ``isolation_level=None``
(autocommit) so the lock + SQLite's BEGIN IMMEDIATE pattern is
sufficient — there's no transaction state to leak across calls.

Pre-debit semantics
-------------------
``check_budget(estimated_input, estimated_output)`` does *both*
the read-and-compare AND a pre-debit of the *estimate* into the
counter, all under the lock.  ``record_usage(actual_in, actual_out,
estimate_in, estimate_out)`` later applies the delta
``actual - estimate`` so the final number reflects real usage.
This is the only way "50 concurrent threads × 10k tokens vs
100k ceiling" can converge to exactly the number that fit —
without a pre-debit, every thread reads "under ceiling" before
any of them increments, and the total over-shoots.

Environment variables
---------------------
- ``GEMINI_BUDGET_DB``        — path to the SQLite file. Defaults
                                to ``<repo_root>/data/gemini_budget.db``.
                                Created on first call.
- ``GEMINI_DAILY_TOKEN_CEILING`` — int. Defaults to 5_000_000
                                (5M tokens/day — conservative; tune
                                via env if real usage warrants).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


DEFAULT_DAILY_TOKEN_CEILING = 5_000_000


class BudgetExceededError(Exception):
    """Raised when a pre-call budget check would push the daily
    counter over the configured ceiling.

    Carries ``used_today`` (current day total *before* the rejected
    call) and ``ceiling`` so callers / exception handlers can include
    them in operator-facing telemetry.  Never echo these into a
    user-visible error body — the operator-facing endpoint at
    ``/admin/gemini-budget`` surfaces the same numbers with an
    explicit gate.
    """

    def __init__(self, used_today: int, ceiling: int) -> None:
        super().__init__(
            f"Daily Gemini token budget exceeded: "
            f"used_today={used_today} ceiling={ceiling}"
        )
        self.used_today = used_today
        self.ceiling = ceiling


# Module-level lock — serializes the check+write critical section.
# Threading.Lock (not asyncio) because the underlying SQLite I/O is
# blocking sync; async callers should hop into asyncio.to_thread / a
# threadpool before touching the budget API.
_LOCK = threading.Lock()


def _repo_root() -> Path:
    # src/utils/gemini_budget.py -> repo root is parents[2].
    return Path(__file__).resolve().parents[2]


def _get_db_path() -> Path:
    """Resolve the SQLite path from env (``GEMINI_BUDGET_DB``) or
    fall back to ``<repo_root>/data/gemini_budget.db``.

    Resolved fresh on every call — tests monkeypatch the env var
    per-test, and a cached path would leak between tests.
    """
    override = os.environ.get("GEMINI_BUDGET_DB", "").strip()
    if override:
        return Path(override)
    return _repo_root() / "data" / "gemini_budget.db"


def _get_ceiling() -> int:
    raw = os.environ.get("GEMINI_DAILY_TOKEN_CEILING", "").strip()
    if not raw:
        return DEFAULT_DAILY_TOKEN_CEILING
    try:
        val = int(raw)
    except ValueError:
        return DEFAULT_DAILY_TOKEN_CEILING
    # Reject non-positive ceilings — they would brick every call.
    # Falling back to the default keeps the breaker permissive on
    # an operator typo (vs. silently denying all AI traffic).
    if val <= 0:
        return DEFAULT_DAILY_TOKEN_CEILING
    return val


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _next_midnight_utc_iso() -> str:
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).date()
    midnight = datetime(
        tomorrow.year, tomorrow.month, tomorrow.day,
        tzinfo=timezone.utc,
    )
    # Use the same ISO format as the GDPR audit row writer: trailing Z.
    return midnight.isoformat(timespec="seconds").replace("+00:00", "Z")


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Open a connection in autocommit mode with WAL journaling.

    WAL gives us concurrent readers + a single writer with no
    fsync-on-every-write tax.  Lazy ``mkdir`` of the parent dir on
    first use so the operator doesn't have to pre-create ``data/``.
    """
    path = _get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=30.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS usage_daily ("
            "  date TEXT PRIMARY KEY,"
            "  input_tokens INTEGER NOT NULL DEFAULT 0,"
            "  output_tokens INTEGER NOT NULL DEFAULT 0"
            ")"
        )
        yield conn
    finally:
        conn.close()


def _ensure_today_row(conn: sqlite3.Connection, today: str) -> None:
    # INSERT OR IGNORE is the standard "upsert with default" pattern in
    # SQLite — no second round-trip when the row already exists.
    conn.execute(
        "INSERT OR IGNORE INTO usage_daily (date, input_tokens, output_tokens) "
        "VALUES (?, 0, 0)",
        (today,),
    )


def _read_today_totals(conn: sqlite3.Connection, today: str) -> tuple[int, int]:
    cur = conn.execute(
        "SELECT input_tokens, output_tokens FROM usage_daily WHERE date = ?",
        (today,),
    )
    row = cur.fetchone()
    if row is None:
        return 0, 0
    return int(row[0]), int(row[1])


def check_budget(estimated_input: int, estimated_output: int) -> None:
    """Pre-call gate: if today's total + the estimate would exceed
    the ceiling, raise ``BudgetExceededError``.  Otherwise pre-debit
    the estimate against today's counter (real usage is reconciled
    later by ``record_usage``).

    Negative estimates are clamped to zero — a misconfigured caller
    shouldn't be able to under-charge the budget below the real spend.
    """
    est_in = max(0, int(estimated_input))
    est_out = max(0, int(estimated_output))
    ceiling = _get_ceiling()
    today = _today_utc()

    with _LOCK:
        with _connect() as conn:
            _ensure_today_row(conn, today)
            cur_in, cur_out = _read_today_totals(conn, today)
            used_today = cur_in + cur_out
            if used_today + est_in + est_out > ceiling:
                raise BudgetExceededError(used_today=used_today, ceiling=ceiling)
            # Pre-debit estimate.  record_usage() will apply the delta
            # actual-minus-estimate so the final total is accurate.
            conn.execute(
                "UPDATE usage_daily "
                "SET input_tokens = input_tokens + ?, "
                "    output_tokens = output_tokens + ? "
                "WHERE date = ?",
                (est_in, est_out, today),
            )


def record_usage(
    actual_input: int,
    actual_output: int,
    estimated_input: int = 0,
    estimated_output: int = 0,
) -> None:
    """Reconcile the per-call estimate (already debited by
    ``check_budget``) with the real usage_metadata that Gemini
    returns post-call.  Adds *non-negative* delta only.

    **Monotonic invariant**: the counter never decreases on
    reconciliation. When a caller's estimate exceeded the actual
    spend (``actual < estimated``), the delta is clamped to zero
    and a WARN log is emitted so the operator can see chronic
    over-estimation. Without this, the ``/admin/gemini-budget``
    snapshot can decrement between consecutive reads — observed
    in PR #274 Phase 9.10 Finding H, where the ``output_today``
    field dropped from 38_532 → 25_887 across two reads (the M3
    cost cap would then leak in the direction that matters:
    under-counts → over-spends). Trade-off: chronic over-estimation
    causes the counter to over-state usage. That is the safer
    direction — better to false-trip the ceiling than to false-pass.

    Defaults for ``estimated_*`` keep the function safe for callers
    that bypass ``check_budget`` entirely (e.g. test fixtures).  In
    that case the full ``actual_*`` is added.
    """
    raw_delta_in = int(actual_input) - int(estimated_input)
    raw_delta_out = int(actual_output) - int(estimated_output)
    if raw_delta_in < 0 or raw_delta_out < 0:
        logger.warning(
            "Gemini estimate exceeded actual — counter held flat to "
            "preserve monotonic invariant. "
            "est_in=%d est_out=%d act_in=%d act_out=%d "
            "(would-be deltas: %d, %d)",
            estimated_input,
            estimated_output,
            actual_input,
            actual_output,
            raw_delta_in,
            raw_delta_out,
        )
    delta_in = max(0, raw_delta_in)
    delta_out = max(0, raw_delta_out)
    today = _today_utc()
    with _LOCK:
        with _connect() as conn:
            # Always ensure today's row exists, even when both clamped
            # deltas are zero — keeps the observable shape consistent for
            # ``get_state`` and for the original M3 contract that today's
            # row is present after any ``record_usage`` call.
            _ensure_today_row(conn, today)
            if delta_in or delta_out:
                conn.execute(
                    "UPDATE usage_daily "
                    "SET input_tokens = input_tokens + ?, "
                    "    output_tokens = output_tokens + ? "
                    "WHERE date = ?",
                    (delta_in, delta_out, today),
                )


def get_state() -> dict[str, object]:
    """Snapshot of today's counters for the admin observability
    endpoint.  Read-only.  ``remaining`` may be negative when
    real usage overshot the estimate; that's a signal, not a bug.
    """
    ceiling = _get_ceiling()
    today = _today_utc()
    with _LOCK:
        with _connect() as conn:
            _ensure_today_row(conn, today)
            cur_in, cur_out = _read_today_totals(conn, today)
    used_today = cur_in + cur_out
    return {
        "date": today,
        "used_today": used_today,
        "input_today": cur_in,
        "output_today": cur_out,
        "ceiling": ceiling,
        "remaining": ceiling - used_today,
        "reset_at_utc": _next_midnight_utc_iso(),
    }
