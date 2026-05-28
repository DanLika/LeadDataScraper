"""Unit tests for ``src/utils/gemini_budget.py``.

Each test points GEMINI_BUDGET_DB at a tmp_path SQLite file so the
real production DB is untouched.  Concurrency test uses
``ThreadPoolExecutor`` to exercise the module-level lock.
"""

from __future__ import annotations

import concurrent.futures
import sqlite3
import sys
from pathlib import Path
from typing import Tuple

import pytest

# Make `src.utils.gemini_budget` importable without a global conftest.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.utils import gemini_budget  # noqa: E402
from src.utils.gemini_budget import (  # noqa: E402
    BudgetExceededError,
    check_budget,
    get_state,
    record_usage,
)


@pytest.fixture
def budget_env(tmp_path, monkeypatch) -> Path:
    """Pin GEMINI_BUDGET_DB at a per-test tmp file and clear ceiling
    override so the default applies unless a test explicitly overrides."""
    db_path = tmp_path / "budget.db"
    monkeypatch.setenv("GEMINI_BUDGET_DB", str(db_path))
    monkeypatch.delenv("GEMINI_DAILY_TOKEN_CEILING", raising=False)
    return db_path


def _read_row(db_path: Path) -> Tuple[str, int, int]:
    """Helper: read today's row directly via sqlite3 to verify schema."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("SELECT date, input_tokens, output_tokens FROM usage_daily")
        rows = cur.fetchall()
        return rows
    finally:
        conn.close()


class TestCheckBudgetUnderCeiling:
    def test_zero_usage_under_ceiling_no_raise(self, budget_env, monkeypatch):
        monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "100000")
        # Single small call — well under ceiling.
        check_budget(100, 200)
        # Pre-debit must have landed in the row.
        rows = _read_row(budget_env)
        assert len(rows) == 1
        _, in_t, out_t = rows[0]
        assert in_t == 100
        assert out_t == 200

    def test_negative_estimate_clamped(self, budget_env, monkeypatch):
        monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "100000")
        check_budget(-50, -50)
        rows = _read_row(budget_env)
        _, in_t, out_t = rows[0]
        assert in_t == 0
        assert out_t == 0

    def test_default_ceiling_when_env_unset(self, budget_env, monkeypatch):
        monkeypatch.delenv("GEMINI_DAILY_TOKEN_CEILING", raising=False)
        state = get_state()
        assert state["ceiling"] == gemini_budget.DEFAULT_DAILY_TOKEN_CEILING

    def test_zero_or_negative_ceiling_env_falls_back_to_default(
        self, budget_env, monkeypatch
    ):
        # An operator typo (e.g. `GEMINI_DAILY_TOKEN_CEILING=0`) must not
        # brick every Gemini call — fall back to the default permissive
        # value instead.
        monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "0")
        state = get_state()
        assert state["ceiling"] == gemini_budget.DEFAULT_DAILY_TOKEN_CEILING
        monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "-9999")
        state = get_state()
        assert state["ceiling"] == gemini_budget.DEFAULT_DAILY_TOKEN_CEILING

    def test_garbage_ceiling_env_falls_back_to_default(self, budget_env, monkeypatch):
        monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "not-an-int")
        state = get_state()
        assert state["ceiling"] == gemini_budget.DEFAULT_DAILY_TOKEN_CEILING


class TestCheckBudgetExceeds:
    def test_call_that_would_exceed_raises(self, budget_env, monkeypatch):
        monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "1000")
        # 500 + 400 = 900, still under 1000 — accept and pre-debit.
        check_budget(500, 400)
        # Now (current 900) + (200) > 1000 — must raise.
        with pytest.raises(BudgetExceededError) as ei:
            check_budget(100, 100)
        assert ei.value.used_today == 900
        assert ei.value.ceiling == 1000
        # On raise the rejected call must NOT have been pre-debited.
        rows = _read_row(budget_env)
        _, in_t, out_t = rows[0]
        assert (in_t, out_t) == (500, 400)

    def test_exact_ceiling_is_accepted(self, budget_env, monkeypatch):
        # The check uses `>` not `>=` — landing exactly on the ceiling
        # is OK.  This pin protects against an off-by-one regression.
        monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "1000")
        check_budget(500, 500)
        state = get_state()
        assert state["used_today"] == 1000
        assert state["remaining"] == 0
        # One token over now raises.
        with pytest.raises(BudgetExceededError):
            check_budget(1, 0)


class TestRecordUsage:
    def test_record_usage_accumulates_same_day(self, budget_env, monkeypatch):
        monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "100000")
        check_budget(100, 200)  # pre-debit 100/200
        # Pretend Gemini returned exactly the estimate — delta zero,
        # final totals unchanged.
        record_usage(100, 200, estimated_input=100, estimated_output=200)
        rows = _read_row(budget_env)
        _, in_t, out_t = rows[0]
        assert in_t == 100
        assert out_t == 200

    def test_record_usage_applies_delta_when_actual_differs(
        self, budget_env, monkeypatch
    ):
        monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "100000")
        check_budget(100, 200)  # pre-debit 100/200
        # Gemini returned more than estimate — counter must catch up.
        record_usage(150, 250, estimated_input=100, estimated_output=200)
        rows = _read_row(budget_env)
        _, in_t, out_t = rows[0]
        assert in_t == 150
        assert out_t == 250

    def test_record_usage_floor_at_zero(self, budget_env, monkeypatch):
        """If a buggy caller passes a SMALLER estimate than the actual
        spend was, the delta is positive and the running total grows.
        Conversely if the estimate was LARGER than the actual, the
        delta is negative — and the row should not go below zero even
        in the pathological case where ``record_usage`` is called
        without a matching pre-debit.  Set up: no pre-debit at all
        (counter starts at 0); call ``record_usage(100, 100,
        estimated=1000, estimated=1000)`` → delta -900/-900 → MAX(0, ...)
        clamps the row at 0 instead of going negative."""
        monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "100000")
        # NO pre-debit — exercise the bare delta path.
        record_usage(100, 100, estimated_input=1000, estimated_output=1000)
        rows = _read_row(budget_env)
        _, in_t, out_t = rows[0]
        assert in_t == 0
        assert out_t == 0

    def test_record_usage_with_default_estimates_adds_full_actual(
        self, budget_env, monkeypatch
    ):
        # A caller that bypasses check_budget entirely passes only
        # actual_input / actual_output.  Default estimates are 0 →
        # full actual is added.
        monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "100000")
        record_usage(500, 600)
        rows = _read_row(budget_env)
        _, in_t, out_t = rows[0]
        assert in_t == 500
        assert out_t == 600


class TestDayBoundary:
    def test_two_distinct_dates_produce_two_rows(self, budget_env, monkeypatch):
        """Day boundary: each public-API call invokes `_today_utc()`
        exactly ONCE.  Cycle two date strings so the first call lands on
        2026-05-23 and the second on 2026-05-24.  Both rows must persist
        in usage_daily."""
        monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "100000")
        import src.utils.gemini_budget as bm

        seq = iter(["2026-05-23", "2026-05-24"])
        monkeypatch.setattr(bm, "_today_utc", lambda: next(seq))
        check_budget(100, 200)  # day1
        check_budget(50, 75)  # day2
        # Both rows present (filter out any auto-created get_state row
        # if a third call slipped in).
        rows = sorted(_read_row(budget_env), key=lambda r: r[0])
        assert len(rows) == 2
        assert rows[0] == ("2026-05-23", 100, 200)
        assert rows[1] == ("2026-05-24", 50, 75)


class TestGetState:
    def test_get_state_shape(self, budget_env, monkeypatch):
        monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "10000")
        check_budget(1000, 2000)
        state = get_state()
        assert set(state.keys()) == {
            "date",
            "used_today",
            "input_today",
            "output_today",
            "ceiling",
            "remaining",
            "reset_at_utc",
        }
        assert state["input_today"] == 1000
        assert state["output_today"] == 2000
        assert state["used_today"] == 3000
        assert state["ceiling"] == 10000
        assert state["remaining"] == 7000
        # reset_at_utc is ISO-Z midnight tomorrow.
        assert state["reset_at_utc"].endswith("Z")
        assert "T00:00:00" in state["reset_at_utc"]

    def test_get_state_remaining_can_be_negative(self, budget_env, monkeypatch):
        """If record_usage reports more than the estimate AFTER the
        pre-debit lands at the ceiling, used_today can briefly exceed
        ceiling — remaining goes negative.  Documented as a signal,
        not a bug."""
        monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "1000")
        check_budget(500, 500)  # debits to 1000, exactly at ceiling
        # Pretend the call cost MORE than the estimate (rare but real).
        record_usage(600, 600, estimated_input=500, estimated_output=500)
        state = get_state()
        assert state["used_today"] == 1200
        assert state["remaining"] == -200


class TestConcurrency:
    def test_50_threads_under_lock_no_overcount(self, budget_env, monkeypatch):
        """50 threads × 10k tokens each, ceiling 100k → exactly 10 must fit
        and the rest must raise.  This is the smoking-gun test for the
        check+pre-debit lock; without it two threads can both read
        ``used_today < ceiling`` before either increments and over-shoot."""
        monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "100000")

        def worker():
            try:
                check_budget(5000, 5000)  # 10k per call
                return ("ok", None)
            except BudgetExceededError as e:
                return ("rejected", e.used_today)

        # 50 attempts, each costs 10k.  Ceiling 100k → exactly 10 succeed.
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
            futures = [ex.submit(worker) for _ in range(50)]
            for f in concurrent.futures.as_completed(futures):
                results.append(f.result())

        oks = [r for r in results if r[0] == "ok"]
        rejected = [r for r in results if r[0] == "rejected"]
        assert len(oks) == 10, (
            f"Expected exactly 10 successes, got {len(oks)}: results={results}"
        )
        assert len(rejected) == 40

        # Final counter must equal the number of successful pre-debits × 10k.
        # No over-count under the lock.
        state = get_state()
        assert state["used_today"] == 100_000

    def test_no_lock_holds_under_sequential_load(self, budget_env, monkeypatch):
        """Sanity: 100 sequential 100-token calls under a 1M ceiling all
        accept and the running total equals the expected sum."""
        monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "1_000_000".replace("_", ""))
        for _ in range(100):
            check_budget(50, 50)
        state = get_state()
        assert state["used_today"] == 100 * 100
