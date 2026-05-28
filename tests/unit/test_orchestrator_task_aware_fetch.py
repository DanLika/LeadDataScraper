"""Unit tests for the task-aware fetch predicate in TaskOrchestrator.

Locks in the Phase 9.10 (PR #274 Finding A) fix: the orchestrator must not
re-fetch a lead that has already completed every requested task. Before the
fix, a job started with ``tasks=['audit']`` re-billed Gemini up to 3 times
per lead because the predicate also tripped on ``enrichment_status='PENDING'``.

Pure-function tests — no DB required, runs in the default offline pytest
sweep.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

# Make the repo importable when running pytest from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.task_orchestrator import TaskOrchestrator


class TestStatusPredicatesForTasks:
    """Static predicate generator — tested without instantiating TaskOrchestrator."""

    def test_audit_only_excludes_completed_and_failed(self):
        pred = TaskOrchestrator._status_predicates_for_tasks(["audit"])
        # Single predicate, NOT IN ('Completed','Failed'). PostgREST
        # syntax: ``column.not.in.(a,b)``.
        assert pred == "audit_status.not.in.(Completed,Failed)"

    def test_enrich_only_excludes_all_terminal_enrichment_states(self):
        pred = TaskOrchestrator._status_predicates_for_tasks(["enrich"])
        # enrichment_status writes COMPLETED, FAILED, or FAILED_NO_CONTENT;
        # all three count as "done" for re-fetch purposes.
        assert pred == "enrichment_status.not.in.(COMPLETED,FAILED,FAILED_NO_CONTENT)"

    def test_audit_and_enrich_joins_with_or(self):
        # The default tasks list. Predicate must be the OR of both — leads
        # missing EITHER status get fetched.
        pred = TaskOrchestrator._status_predicates_for_tasks(["audit", "enrich"])
        parts = pred.split(",")
        # PostgREST syntax includes a comma inside the .in.(a,b) form, so
        # parts may include literal commas from the IN list. Use a tighter
        # assertion than a naive split-count.
        assert "audit_status.not.in.(Completed" in pred
        assert "Failed)" in pred
        assert "enrichment_status.not.in.(COMPLETED" in pred
        assert "FAILED" in pred
        assert "FAILED_NO_CONTENT)" in pred

    def test_hunt_only_falls_back_to_historical_predicate(self):
        # No hunt_status column exists yet — preserve old behavior so
        # an explicit ``tasks=['hunt']`` caller does not start touching
        # leads where audit/enrich are already done.
        pred = TaskOrchestrator._status_predicates_for_tasks(["hunt"])
        assert "audit_status.neq.Completed" in pred
        assert "enrichment_status.neq.COMPLETED" in pred

    def test_empty_tasks_defaults_to_audit_plus_enrich(self):
        # ``None`` and ``[]`` both map to the default tasks list.
        pred_none = TaskOrchestrator._status_predicates_for_tasks(None)
        pred_empty = TaskOrchestrator._status_predicates_for_tasks([])
        # Empty list path goes through the fail-safe branch, not the
        # default-fill branch, so they differ in form but both select a
        # safe superset.
        for p in (pred_none, pred_empty):
            assert "audit_status" in p
            assert "enrichment_status" in p

    def test_unknown_tasks_failsafe_does_not_select_everything(self):
        # An unknown task name must not silently select every row.
        pred = TaskOrchestrator._status_predicates_for_tasks(["pancakes"])
        assert pred  # non-empty
        assert "audit_status" in pred or "enrichment_status" in pred

    def test_predicate_is_pure_function(self):
        # Calling twice with the same arg yields the same string. Important
        # because PostgREST cached predicates would mis-fire if the
        # generator embedded mutable state.
        a = TaskOrchestrator._status_predicates_for_tasks(["audit"])
        b = TaskOrchestrator._status_predicates_for_tasks(["audit"])
        assert a == b


class TestFetchChunkThreadsTasks:
    """Spot-check that ``_fetch_chunk`` actually passes the task-derived
    predicate to PostgREST. We don't run a real DB; we capture the
    ``.or_()`` argument from the fluent-builder chain.
    """

    def _make_orchestrator_with_mock_client(self) -> tuple[TaskOrchestrator, MagicMock]:
        orch = TaskOrchestrator.__new__(TaskOrchestrator)
        orch.db = MagicMock()
        # Mimic the supabase-py builder chain so we can introspect each call.
        builder = MagicMock()
        builder.select.return_value = builder
        builder.or_.return_value = builder
        builder.lt.return_value = builder
        builder.order.return_value = builder
        builder.limit.return_value = builder
        builder.execute.return_value = MagicMock(data=[])
        orch.db.client.table.return_value = builder
        return orch, builder

    def test_audit_only_passes_narrow_predicate(self):
        orch, builder = self._make_orchestrator_with_mock_client()
        orch._fetch_chunk(
            lead_ids=None,
            processed_count=0,
            chunk_size=50,
            total_leads=0,
            tasks=["audit"],
        )
        builder.or_.assert_called_once()
        predicate = builder.or_.call_args[0][0]
        assert predicate == "audit_status.not.in.(Completed,Failed)"
        # `enrichment_status` MUST be absent — that's the whole point of the
        # task-aware fix.
        assert "enrichment_status" not in predicate

    def test_default_tasks_pass_both_status_predicates(self):
        orch, builder = self._make_orchestrator_with_mock_client()
        orch._fetch_chunk(
            lead_ids=None, processed_count=0, chunk_size=50, total_leads=0, tasks=None
        )
        predicate = builder.or_.call_args[0][0]
        assert "audit_status" in predicate
        assert "enrichment_status" in predicate

    def test_lead_ids_path_skips_predicate(self):
        # Explicit lead_ids list → no status predicate at all (caller knows
        # exactly which rows to process). Belt-and-braces against the
        # regression vector where a future refactor accidentally adds the
        # predicate on the lead_ids branch too.
        orch, builder = self._make_orchestrator_with_mock_client()
        builder.in_.return_value = builder
        orch._fetch_chunk(
            lead_ids=["x1", "x2"],
            processed_count=0,
            chunk_size=50,
            total_leads=2,
            tasks=["audit"],
        )
        builder.or_.assert_not_called()


class TestGetTotalLeadsThreadsTasks:
    """Same spot-check for the count query that drives total_count in the
    job status payload.
    """

    def test_count_query_uses_task_aware_predicate(self):
        orch = TaskOrchestrator.__new__(TaskOrchestrator)
        orch.db = MagicMock()
        builder = MagicMock()
        for fn in ("select", "or_", "lt", "eq"):
            getattr(builder, fn).return_value = builder
        builder.execute.return_value = MagicMock(count=0)
        orch.db.client.table.return_value = builder

        orch._get_total_leads(lead_ids=None, filters={}, tasks=["audit"])

        builder.or_.assert_called_once()
        predicate = builder.or_.call_args[0][0]
        assert "audit_status" in predicate
        assert "enrichment_status" not in predicate
