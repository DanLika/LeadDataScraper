"""Unit tests for src.workers.webhook_sweeper.

The sweeper recovers webhook_events rows stranded by transport-class
errors in the inbound handler (see PR #357 backlog + the
``bug_webhook_burst_stranded_rows_2026-05-27`` memory). These tests
pin:

  * Empty queue → clean no-op.
  * Stranded rows → all dispatched to ``_process_instantly_event``.
  * Grace window honored (recent rows skipped — protects in-flight
    BackgroundTask from preemption).
  * Batch size honored (Render Cron 60s budget).
  * Per-row handler exception captured; batch continues.
  * Non-Instantly providers skipped (forward-compat for Resend /
    HeyReach handlers that haven't shipped yet).
  * DB client missing → fast 1-error return.
  * Runtime cap stops mid-batch with structured ``runtime_cap`` error.

The supabase chain mock mirrors ``tests/test_instantly_webhook.py``
shape but only models the read paths the sweeper actually uses.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest


def _row(event_id: str, *, age_seconds: int = 120, provider: str = "instantly") -> dict:
    return {
        "id": hash(event_id) & 0xFFFFFFFF,
        "provider": provider,
        "event_id": event_id,
        "event_type": "email_sent",
        "payload": {
            "event_id": event_id,
            "event_type": "email_sent",
            "recipient_email": f"{event_id}@test.invalid",
        },
        "received_at": (
            datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
        ).isoformat(),
    }


class _ChainMock:
    """Reusable supabase-py chain mock — records the read predicates
    so tests can assert grace window + batch size were applied."""

    def __init__(self, rows: list[dict]) -> None:
        self._all_rows = list(rows)
        self.captured: dict[str, Any] = {}
        self._chain = MagicMock()
        self._chain.select.return_value = self._chain
        self._chain.is_.side_effect = self._cap_is
        self._chain.lt.side_effect = self._cap_lt
        self._chain.order.side_effect = self._cap_order
        self._chain.limit.side_effect = self._cap_limit
        self._chain.execute.side_effect = self._execute

    def _cap_is(self, col, val):
        self.captured.setdefault("is_", []).append((col, val))
        return self._chain

    def _cap_lt(self, col, val):
        self.captured.setdefault("lt", []).append((col, val))
        return self._chain

    def _cap_order(self, col, desc: bool = False):
        self.captured["order"] = (col, desc)
        return self._chain

    def _cap_limit(self, n):
        self.captured["limit"] = n
        return self._chain

    def _execute(self):
        rows = self._all_rows
        # Apply the lt(received_at, cutoff) predicate so the grace
        # window test can rely on it instead of pre-filtering.
        lts = self.captured.get("lt", [])
        for col, val in lts:
            if col == "received_at":
                rows = [r for r in rows if r["received_at"] < val]
        limit = self.captured.get("limit")
        if limit is not None:
            rows = rows[:limit]
        return MagicMock(data=rows)


class _Db:
    """db wrapper shape matched to src.utils.supabase_helper.db."""

    def __init__(self, *, rows: list[dict] | None = None, client: bool = True) -> None:
        self.chain = _ChainMock(rows or [])
        if client:
            self.client = MagicMock()
            self.client.table.return_value = self.chain._chain
        else:
            self.client = None


def _async_recorder():
    """Returns (recorder list, async fn). Each call appends a tuple
    ``(event_id, payload)`` to the recorder for assertion."""
    recorder: list[tuple[str, dict]] = []

    async def _fn(*, event_id: str, payload: dict) -> None:
        recorder.append((event_id, dict(payload)))

    return recorder, _fn


def _async_raises():
    """Returns an async fn that raises RuntimeError on every call,
    plus a counter dict so tests can assert per-row dispatch shape."""
    state = {"calls": 0}

    async def _fn(*, event_id: str, payload: dict) -> None:
        state["calls"] += 1
        raise RuntimeError(f"handler-blew-up:{event_id}")

    return state, _fn


@pytest.mark.asyncio
async def test_empty_queue_returns_zero_counts():
    from src.workers.webhook_sweeper import sweep_once

    db = _Db(rows=[])
    recorder, fn = _async_recorder()
    result = await sweep_once(db=db, process_instantly_event=fn)

    assert result.scanned == 0
    assert result.processed == 0
    assert result.failed == 0
    assert result.errors == []
    assert recorder == []


@pytest.mark.asyncio
async def test_stranded_rows_all_processed():
    from src.workers.webhook_sweeper import sweep_once

    rows = [_row(f"e-{i}", age_seconds=180) for i in range(3)]
    db = _Db(rows=rows)
    recorder, fn = _async_recorder()

    result = await sweep_once(db=db, process_instantly_event=fn)

    assert result.scanned == 3
    assert result.processed == 3
    assert result.failed == 0
    assert {eid for eid, _ in recorder} == {"e-0", "e-1", "e-2"}


@pytest.mark.asyncio
async def test_grace_window_applied():
    from src.workers.webhook_sweeper import sweep_once

    rows = [
        _row("recent", age_seconds=10),  # inside grace; must be skipped
        _row("aged-1", age_seconds=120),  # past grace
        _row("aged-2", age_seconds=300),
    ]
    db = _Db(rows=rows)
    recorder, fn = _async_recorder()

    result = await sweep_once(
        db=db,
        process_instantly_event=fn,
        grace_seconds=60,
    )

    seen = {eid for eid, _ in recorder}
    assert "recent" not in seen
    assert seen == {"aged-1", "aged-2"}
    assert result.processed == 2


@pytest.mark.asyncio
async def test_batch_size_caps_claim():
    from src.workers.webhook_sweeper import sweep_once

    rows = [_row(f"e-{i}", age_seconds=180) for i in range(10)]
    db = _Db(rows=rows)
    recorder, fn = _async_recorder()

    result = await sweep_once(
        db=db,
        process_instantly_event=fn,
        batch_size=4,
    )

    assert db.chain.captured.get("limit") == 4
    assert result.scanned == 4
    assert result.processed == 4
    assert len(recorder) == 4


@pytest.mark.asyncio
async def test_handler_exception_continues_batch():
    from src.workers.webhook_sweeper import sweep_once

    rows = [_row(f"e-{i}", age_seconds=180) for i in range(3)]
    db = _Db(rows=rows)
    state, raising_fn = _async_raises()

    result = await sweep_once(db=db, process_instantly_event=raising_fn)

    assert result.scanned == 3
    assert result.processed == 0
    assert result.failed == 3
    assert state["calls"] == 3, (
        "every row attempted; one poison doesn't block the batch"
    )
    assert all("RuntimeError" in err for err in result.errors)


@pytest.mark.asyncio
async def test_non_instantly_provider_skipped():
    from src.workers.webhook_sweeper import sweep_once

    rows = [
        _row("inst-1", age_seconds=180, provider="instantly"),
        _row("res-1", age_seconds=180, provider="resend"),
    ]
    db = _Db(rows=rows)
    recorder, fn = _async_recorder()

    result = await sweep_once(db=db, process_instantly_event=fn)

    assert result.scanned == 2
    assert result.processed == 1
    assert result.skipped == 1
    assert recorder == [("inst-1", rows[0]["payload"])]


@pytest.mark.asyncio
async def test_db_client_missing_returns_error():
    from src.workers.webhook_sweeper import sweep_once

    db = _Db(client=False)
    recorder, fn = _async_recorder()

    result = await sweep_once(db=db, process_instantly_event=fn)

    assert result.scanned == 0
    assert "db_client_unavailable" in result.errors
    assert recorder == []


@pytest.mark.asyncio
async def test_runtime_cap_stops_mid_batch():
    from src.workers.webhook_sweeper import sweep_once

    rows = [_row(f"e-{i}", age_seconds=180) for i in range(5)]
    db = _Db(rows=rows)
    state = {"calls": 0}

    async def slow_fn(*, event_id: str, payload: dict) -> None:
        state["calls"] += 1
        # Each call burns 60ms; with max_runtime_sec=0 the FIRST
        # iteration's deadline check already trips.
        await asyncio.sleep(0.06)

    result = await sweep_once(
        db=db,
        process_instantly_event=slow_fn,
        max_runtime_sec=0,
    )

    assert "runtime_cap" in result.errors
    assert result.processed < len(rows)
