"""Regression test for the run_massive_pipeline dedup race.

Pins the fix for ``race_conditions_2026-05-30`` T4: two concurrent calls to
``/process-all`` + ``/hunt-all`` produced 2 distinct job_ids because

  * the INSERT wrote ``status='starting'``;
  * ``find_running_job`` filters ``status=eq.running``;
  * the ``starting -> running`` transition was deferred into the detached
    ``_process_in_chunks`` task, which had not yet executed when the second
    caller acquired the ``_job_lock`` and queried.

The fix writes ``status='running'`` directly on INSERT so the
``_job_lock``-guarded sequence (find_running_job -> INSERT) sees a
just-started job from a sibling call. This test would have caught the bug
before it reached prod.

Pure-function tests — no DB, no network. Mocks the SupabaseHelper layer.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.task_orchestrator import TaskOrchestrator


def _make_orchestrator_with_in_memory_jobs() -> tuple[TaskOrchestrator, List[Dict[str, Any]]]:
    """Build a TaskOrchestrator whose db layer is backed by an in-memory list.

    Returns the orchestrator + the backing list so the test can inspect it
    after the run. Mocks the supabase client so __init__ does not try to
    open a real network connection.
    """
    jobs: List[Dict[str, Any]] = []

    with patch("src.core.task_orchestrator.SupabaseHelper") as MockHelper:
        helper = MockHelper.return_value
        helper.client = MagicMock()

        async def fake_find_running_job() -> List[Dict[str, Any]]:
            return [row for row in jobs if row.get("status") == "running"]

        async def fake_insert(job_data: Dict[str, Any]) -> None:
            jobs.append(dict(job_data))

        helper.find_running_job = AsyncMock(side_effect=fake_find_running_job)
        helper.insert_orchestration_job = AsyncMock(side_effect=fake_insert)
        orchestrator = TaskOrchestrator()

    return orchestrator, jobs


@pytest.mark.asyncio
async def test_concurrent_run_massive_pipeline_dedups_to_single_job() -> None:
    """Two concurrent calls without lead_ids must collapse to one job_id.

    Asserts the fix: INSERT writes status='running' (not 'starting'), so
    the second caller's ``find_running_job`` sees the first caller's row
    even when ``_process_in_chunks`` has not yet executed.
    """
    orchestrator, jobs = _make_orchestrator_with_in_memory_jobs()

    # Block _process_in_chunks: in real life it kicks off an audit pass
    # that runs for minutes. Replace with a no-op so the test does not
    # require any background work to complete.
    async def no_op_process_in_chunks(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.sleep(0)

    orchestrator._process_in_chunks = no_op_process_in_chunks  # type: ignore[method-assign]

    # Fire two concurrent calls (mimics the T4 race: /process-all + /hunt-all).
    results = await asyncio.gather(
        orchestrator.run_massive_pipeline(tasks=["audit"]),
        orchestrator.run_massive_pipeline(tasks=["hunt"]),
    )

    distinct = set(results)
    assert len(distinct) == 1, (
        f"dedup race: two concurrent run_massive_pipeline returned distinct "
        f"job_ids {results}. The lock+find_running_job gate must serialize."
    )
    # And only one orchestration_jobs row was inserted.
    assert len(jobs) == 1, (
        f"INSERT race: {len(jobs)} job rows landed for 2 concurrent calls; "
        f"expected 1."
    )
    # And that row was written as status='running' directly so a peer's
    # find_running_job will see it (this is the fix).
    assert jobs[0]["status"] == "running", (
        f"orchestrator wrote status={jobs[0]['status']!r} on INSERT — should "
        f"be 'running' so find_running_job catches it under contention."
    )


@pytest.mark.asyncio
async def test_serial_second_call_returns_existing_job_id() -> None:
    """Non-concurrent dedup still works: a second call after the first
    completes its lock-protected INSERT must return the first job_id.

    This is the post-fix happy path — kept separate from the concurrent
    test so a regression that breaks ONLY the serial path (e.g. dropping
    the find_running_job query entirely) gets caught with a distinct
    failure message.
    """
    orchestrator, jobs = _make_orchestrator_with_in_memory_jobs()

    async def no_op_process_in_chunks(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.sleep(0)

    orchestrator._process_in_chunks = no_op_process_in_chunks  # type: ignore[method-assign]

    first = await orchestrator.run_massive_pipeline(tasks=["audit"])
    second = await orchestrator.run_massive_pipeline(tasks=["hunt"])

    assert first == second, (
        f"serial dedup broken: second call got {second}, expected {first}"
    )
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_explicit_lead_ids_always_creates_new_job() -> None:
    """When lead_ids is passed, dedup is intentionally bypassed — each
    explicit-IDs request gets its own job.

    Pinned because the fix is in the ``if not lead_ids:`` branch only;
    a future refactor that flattens the conditional must not accidentally
    dedup explicit-IDs jobs (would silently drop /process-lead work).
    """
    orchestrator, jobs = _make_orchestrator_with_in_memory_jobs()

    async def no_op_process_in_chunks(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.sleep(0)

    orchestrator._process_in_chunks = no_op_process_in_chunks  # type: ignore[method-assign]

    first = await orchestrator.run_massive_pipeline(lead_ids=["k1"], tasks=["audit"])
    second = await orchestrator.run_massive_pipeline(lead_ids=["k2"], tasks=["audit"])

    assert first != second, (
        f"explicit-lead_ids dedup leaked: both calls returned {first}"
    )
    assert len(jobs) == 2
