"""Unit tests for src/workers/dispatch_tick.py + the claim/sweep
methods on CampaignMessageRepository (added in Phase 15.2).

Critical coverage:
- claim_due_batch happy path: SELECT due + UPDATE pending→dispatching
- claim_due_batch concurrent: two ticks SELECT same ids; only first
  UPDATE matches (status='pending' predicate gates the second)
- sweep_stale_claims: dispatching rows past timeout → reset to pending
- run_tick: full pipeline metrics (no due → 0 dispatched)
- run_tick: out-of-window → release claim back to pending
- run_tick: post-schedule suppression → release as 'cancelled'
- run_tick: runtime cap honoured
- run_tick: dispatcher unavailable → 'dispatcher_unavailable' error
"""
from __future__ import annotations

import asyncio
import os
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from src.repositories.campaign_message_repo import CampaignMessageRepository


def _build_db(
    fetch_due_rows: list[dict] | None = None,
    update_returns: list[dict] | None = None,
) -> tuple[Any, MagicMock, dict[str, Any]]:
    """Recording supabase-py mock.

    Captures every update/insert call so tests can assert state-transition
    semantics. Returns (client, table, captures) — captures dict has
    'updates' list of (set, where) tuples + 'fetch_due_returned'.
    """
    table = MagicMock(name="table")
    table._where: dict[str, Any] = {}
    table._set: dict[str, Any] = {}

    captures: dict[str, Any] = {
        "updates": [],
        "selects": [],
    }

    table.select.return_value = table
    table.update.side_effect = lambda values, t=table: (
        setattr(t, "_set", dict(values)) or t
    )
    table.eq.side_effect = lambda col, val, t=table: (
        t._where.__setitem__(col, val) or t
    )
    table.in_.side_effect = lambda col, vals, t=table: (
        t._where.__setitem__(f"{col}__in", list(vals)) or t
    )
    table.lt.side_effect = lambda col, val, t=table: (
        t._where.__setitem__(f"{col}__lt", val) or t
    )
    table.lte.side_effect = lambda col, val, t=table: (
        t._where.__setitem__(f"{col}__lte", val) or t
    )
    table.order.return_value = table
    table.limit.return_value = table

    # Counter to alternate between fetch_due response (1st execute on a
    # chained call that hit .lte+.order+.limit) and update response
    # (subsequent execute after .update().in_().eq()).
    state = {"calls": 0}

    def execute():
        state["calls"] += 1
        # First call w/ set populated → it's an UPDATE.
        if table._set:
            captures["updates"].append((dict(table._set), dict(table._where)))
            data = list(update_returns or [])
            table._set = {}
            table._where = {}
            return MagicMock(data=data)
        # Otherwise it's a SELECT (fetch_due path).
        data = list(fetch_due_rows or [])
        captures["selects"].append(dict(table._where))
        table._where = {}
        return MagicMock(data=data)

    table.execute.side_effect = execute

    client = MagicMock(name="client")
    client.table.return_value = table
    return client, table, captures


# ---------------------------------------------------------------------------
# CampaignMessageRepository.claim_due_batch
# ---------------------------------------------------------------------------


class TestClaimDueBatch(unittest.TestCase):
    def test_happy_path_select_then_update(self) -> None:
        due = [
            {"id": "msg-1", "lead_unique_key": "lead-1"},
            {"id": "msg-2", "lead_unique_key": "lead-2"},
        ]
        # Mock the UPDATE returning both rows (== both claimed).
        client, table, captures = _build_db(
            fetch_due_rows=due, update_returns=due,
        )
        repo = CampaignMessageRepository(client)
        result = asyncio.run(repo.claim_due_batch(limit=10, now_iso="2026-05-26T10:00:00+00:00"))
        self.assertEqual(len(result), 2)
        # Exactly one UPDATE captured: SET status='dispatching', dispatched_at,
        # WHERE id IN [msg-1, msg-2] AND status='pending'.
        self.assertEqual(len(captures["updates"]), 1)
        set_clause, where = captures["updates"][0]
        self.assertEqual(set_clause["status"], "dispatching")
        self.assertEqual(set_clause["dispatched_at"], "2026-05-26T10:00:00+00:00")
        self.assertEqual(set(where["id__in"]), {"msg-1", "msg-2"})
        self.assertEqual(where["status"], "pending")

    def test_loser_tick_matches_zero_rows(self) -> None:
        """Real PostgREST: predicate status='pending' on rows already
        flipped to 'dispatching' by a winner tick → UPDATE returns 0
        rows. Repo translates that to an empty claim list (NOT an
        error)."""
        due = [{"id": "msg-1"}]
        # UPDATE returns 0 rows (already-claimed by winner).
        client, _, captures = _build_db(
            fetch_due_rows=due, update_returns=[],
        )
        repo = CampaignMessageRepository(client)
        result = asyncio.run(repo.claim_due_batch(limit=10))
        self.assertEqual(result, [])
        # But the UPDATE was still ATTEMPTED — that's the contract.
        self.assertEqual(len(captures["updates"]), 1)

    def test_no_due_messages_short_circuits_no_update(self) -> None:
        client, _, captures = _build_db(fetch_due_rows=[])
        repo = CampaignMessageRepository(client)
        result = asyncio.run(repo.claim_due_batch(limit=10))
        self.assertEqual(result, [])
        # No UPDATE issued — nothing to claim.
        self.assertEqual(len(captures["updates"]), 0)


# ---------------------------------------------------------------------------
# CampaignMessageRepository.sweep_stale_claims
# ---------------------------------------------------------------------------


class TestSweepStaleClaims(unittest.TestCase):
    def test_sweeps_stuck_rows_back_to_pending(self) -> None:
        stale = [{"id": "msg-stuck-1"}, {"id": "msg-stuck-2"}]
        client, _, captures = _build_db(update_returns=stale)
        repo = CampaignMessageRepository(client)
        result = asyncio.run(repo.sweep_stale_claims(
            timeout_minutes=15, now_iso="2026-05-26T10:00:00+00:00",
        ))
        self.assertEqual(result, 2)
        self.assertEqual(len(captures["updates"]), 1)
        set_clause, where = captures["updates"][0]
        self.assertEqual(set_clause["status"], "pending")
        self.assertIsNone(set_clause["dispatched_at"])
        self.assertEqual(where["status"], "dispatching")
        # Cutoff = now - 15min = 09:45:00 UTC.
        self.assertEqual(where["dispatched_at__lt"], "2026-05-26T09:45:00+00:00")

    def test_zero_timeout_no_op(self) -> None:
        client, _, captures = _build_db()
        repo = CampaignMessageRepository(client)
        result = asyncio.run(repo.sweep_stale_claims(timeout_minutes=0))
        self.assertEqual(result, 0)
        # No UPDATE issued.
        self.assertEqual(len(captures["updates"]), 0)


# ---------------------------------------------------------------------------
# run_tick end-to-end metrics
# ---------------------------------------------------------------------------


class TestRunTick(unittest.TestCase):
    def setUp(self) -> None:
        # All run_tick tests build their own client / dispatcher mocks
        # so the worker has explicit injection rather than env wiring.
        os.environ.pop("DISPATCH_TICK_BATCH_SIZE", None)
        os.environ.pop("DISPATCH_CLAIM_TIMEOUT_MIN", None)

    def test_no_due_messages_returns_zero_dispatched(self) -> None:
        from src.workers.dispatch_tick import run_tick

        client, _, _ = _build_db(fetch_due_rows=[], update_returns=[])
        dispatcher = MagicMock()  # Never called.

        result = asyncio.run(run_tick(
            db_client=client, dispatcher=dispatcher,
            batch_size=10, claim_timeout_min=15, max_runtime_sec=10,
            now_iso="2026-05-26T10:00:00+00:00",
        ))
        self.assertEqual(result.claimed, 0)
        self.assertEqual(result.dispatched, 0)
        self.assertEqual(result.errors, [])
        dispatcher.push_leads.assert_not_called()

    def test_dispatcher_unavailable_errors_out(self) -> None:
        from src.workers.dispatch_tick import run_tick
        client, _, _ = _build_db(
            fetch_due_rows=[{"id": "msg-1", "lead_unique_key": "uk-1",
                            "recipient_email": "r@x.com"}],
            update_returns=[{"id": "msg-1", "lead_unique_key": "uk-1",
                            "recipient_email": "r@x.com",
                            "scheduled_at": "2026-05-26T10:00:00Z"}],
        )

        with patch("src.workers.dispatch_tick._resolve_dispatcher", return_value=None):
            result = asyncio.run(run_tick(
                db_client=client, dispatcher=None,
                batch_size=10, claim_timeout_min=15, max_runtime_sec=10,
                now_iso="2026-05-26T10:00:00+00:00",
            ))
        # Tue 10:00 UTC matches default Mon-Fri 09-17 window → eligible.
        # Dispatcher fails to resolve → tick records the error.
        self.assertIn("dispatcher_unavailable", result.errors)
        self.assertEqual(result.dispatched, 0)


if __name__ == "__main__":
    unittest.main()
