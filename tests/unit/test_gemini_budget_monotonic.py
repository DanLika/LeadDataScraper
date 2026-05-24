"""Regression tests for Phase 9.10 (PR #274) Finding H.

The M3 cost-cap counter exposed by ``GET /admin/gemini-budget`` was
observed to DECREMENT between two consecutive reads — same headers,
single uvicorn worker, monotonic wall clock. Root cause: ``record_usage``
applied a *negative* delta when the per-call estimate exceeded the actual
token usage that Gemini reports post-call. The clamp ``MAX(0, x + delta)``
inside SQL only floored the FINAL value at zero; it still allowed the
counter to drop from its prior value when ``delta < 0``.

After the fix, the contract is **monotonic**: the counter only ever
increases (or stays flat) over the life of a UTC day. Over-estimation
emits a WARN log instead of subtracting from the counter.

Includes a concurrent-writer probe — 50 parallel ``check_budget +
record_usage`` cycles on a temp DB, final value must equal the sum
(no lost updates).
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.utils import gemini_budget


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Per-test SQLite file. Avoids state leak between tests."""
    db_path = tmp_path / "budget.db"
    monkeypatch.setenv("GEMINI_BUDGET_DB", str(db_path))
    # Reset env ceiling to default so tests don't surprise each other
    monkeypatch.delenv("GEMINI_DAILY_TOKEN_CEILING", raising=False)
    yield db_path


class TestMonotonicCounter:
    def test_over_estimate_does_not_decrement_counter(self, temp_db):
        """The exact pattern from PR #274 Finding H."""
        # Pre-debit a generous estimate.
        gemini_budget.check_budget(estimated_input=10_000, estimated_output=5_000)
        s_before = gemini_budget.get_state()
        assert s_before["input_today"] == 10_000
        assert s_before["output_today"] == 5_000

        # Actual usage came in lower than estimate.
        gemini_budget.record_usage(
            actual_input=8_000,
            actual_output=2_000,
            estimated_input=10_000,
            estimated_output=5_000,
        )

        s_after = gemini_budget.get_state()
        # Monotonic: counter must NOT have decreased on any axis.
        assert s_after["input_today"] >= s_before["input_today"]
        assert s_after["output_today"] >= s_before["output_today"]
        assert s_after["used_today"] >= s_before["used_today"]
        # In particular: unchanged because delta would have been negative.
        assert s_after["input_today"] == 10_000
        assert s_after["output_today"] == 5_000

    def test_under_estimate_increments_counter(self, temp_db):
        gemini_budget.check_budget(estimated_input=1_000, estimated_output=500)
        s_before = gemini_budget.get_state()
        gemini_budget.record_usage(
            actual_input=3_000,
            actual_output=800,
            estimated_input=1_000,
            estimated_output=500,
        )
        s_after = gemini_budget.get_state()
        assert s_after["input_today"] == s_before["input_today"] + 2_000
        assert s_after["output_today"] == s_before["output_today"] + 300

    def test_exact_estimate_no_op(self, temp_db):
        gemini_budget.check_budget(estimated_input=500, estimated_output=200)
        s_before = gemini_budget.get_state()
        gemini_budget.record_usage(
            actual_input=500, actual_output=200,
            estimated_input=500, estimated_output=200,
        )
        s_after = gemini_budget.get_state()
        assert s_after == s_before

    def test_no_estimate_writes_full_actual(self, temp_db):
        # When a caller bypasses check_budget (test fixtures, etc.),
        # record_usage with no estimate should add the full actual.
        s_before = gemini_budget.get_state()
        gemini_budget.record_usage(actual_input=1_000, actual_output=300)
        s_after = gemini_budget.get_state()
        assert s_after["input_today"] == s_before["input_today"] + 1_000
        assert s_after["output_today"] == s_before["output_today"] + 300

    def test_over_estimate_emits_warning(self, temp_db, caplog):
        import logging
        caplog.set_level(logging.WARNING, logger="src.utils.gemini_budget")
        gemini_budget.check_budget(estimated_input=10_000, estimated_output=5_000)
        gemini_budget.record_usage(
            actual_input=4_000, actual_output=1_000,
            estimated_input=10_000, estimated_output=5_000,
        )
        assert any(
            "estimate exceeded actual" in rec.message
            for rec in caplog.records
        )


class TestConcurrentIncrements:
    """50 parallel check+record cycles. Final counter == sum of inputs.

    Belt-and-braces against any future regression where the per-process
    threading.Lock is removed or the SQLite UPDATE loses serializability.
    """

    def test_fifty_concurrent_increments_no_lost_updates(self, temp_db):
        N = 50
        PER_CALL_INPUT = 100
        PER_CALL_OUTPUT = 50

        # Bump the ceiling high enough that none of the parallel
        # check_budget calls trips it.
        os.environ["GEMINI_DAILY_TOKEN_CEILING"] = str(10 * N * (PER_CALL_INPUT + PER_CALL_OUTPUT))
        try:
            def worker():
                gemini_budget.check_budget(PER_CALL_INPUT, PER_CALL_OUTPUT)
                gemini_budget.record_usage(
                    PER_CALL_INPUT, PER_CALL_OUTPUT,
                    PER_CALL_INPUT, PER_CALL_OUTPUT,
                )

            threads = [threading.Thread(target=worker) for _ in range(N)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            s = gemini_budget.get_state()
            assert s["input_today"] == N * PER_CALL_INPUT
            assert s["output_today"] == N * PER_CALL_OUTPUT
        finally:
            os.environ.pop("GEMINI_DAILY_TOKEN_CEILING", None)


class TestGetStateStable:
    """Two reads of get_state() with no writes in between must agree."""

    def test_repeated_reads_return_same_snapshot(self, temp_db):
        gemini_budget.check_budget(1_000, 500)
        s1 = gemini_budget.get_state()
        s2 = gemini_budget.get_state()
        s3 = gemini_budget.get_state()
        assert s1["input_today"] == s2["input_today"] == s3["input_today"]
        assert s1["output_today"] == s2["output_today"] == s3["output_today"]
        assert s1["used_today"] == s2["used_today"] == s3["used_today"]
