"""Phase 15.1 additions to CampaignMessageRepository.

- fetch_due_for_dispatch(limit, now_iso): partial-index hot path
- schedule_step(message_id, step_id, variant_id, scheduled_at_iso):
  state-machine-gated UPDATE (only fires on status='pending')

Phase 14.3 surface tested separately in tests/unit/test_campaign_message_repo.py.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import MagicMock

from src.repositories.campaign_message_repo import (
    CampaignMessageRepository,
)


def _build_db(rows: list[dict[str, Any]] | None = None) -> tuple[Any, MagicMock]:
    table = MagicMock(name="table")
    table.select.return_value = table
    table.update.return_value = table
    table.eq.return_value = table
    table.lte.return_value = table
    table.order.return_value = table
    table.limit.return_value = table

    class _Result:
        def __init__(self, data: list[dict[str, Any]]) -> None:
            self.data = data

    table.execute.return_value = _Result(rows or [])
    client = MagicMock(name="client")
    client.table.return_value = table
    return client, table


class TestFetchDueForDispatch(unittest.TestCase):
    def test_returns_due_rows(self) -> None:
        rows = [
            {
                "id": f"msg-{i}",
                "status": "pending",
                "scheduled_at": "2026-05-25T09:00:00Z",
                "campaign_id": "c-1",
                "lead_unique_key": f"lead-{i}",
            }
            for i in range(3)
        ]
        client, table = _build_db(rows)
        repo = CampaignMessageRepository(client)
        result = asyncio.run(repo.fetch_due_for_dispatch(limit=10))
        self.assertEqual(len(result), 3)
        # Predicate chain.
        table.eq.assert_called_with("status", "pending")
        table.lte.assert_called()
        lte_args = table.lte.call_args.args
        self.assertEqual(lte_args[0], "scheduled_at")
        table.order.assert_called_with("scheduled_at", desc=False)
        table.limit.assert_called_with(10)

    def test_default_limit(self) -> None:
        client, table = _build_db([])
        repo = CampaignMessageRepository(client)
        asyncio.run(repo.fetch_due_for_dispatch())
        table.limit.assert_called_with(100)

    def test_zero_or_negative_limit_short_circuits(self) -> None:
        client, _ = _build_db([{"id": "x"}])
        repo = CampaignMessageRepository(client)
        self.assertEqual(asyncio.run(repo.fetch_due_for_dispatch(limit=0)), [])
        self.assertEqual(asyncio.run(repo.fetch_due_for_dispatch(limit=-1)), [])
        client.table.assert_not_called()

    def test_custom_now_iso_passed_through(self) -> None:
        client, table = _build_db([])
        repo = CampaignMessageRepository(client)
        asyncio.run(repo.fetch_due_for_dispatch(now_iso="2026-01-01T00:00:00Z"))
        lte_args = table.lte.call_args.args
        self.assertEqual(lte_args[1], "2026-01-01T00:00:00Z")

    def test_db_exception_returns_empty_list(self) -> None:
        client, table = _build_db([])
        table.execute.side_effect = RuntimeError("connection refused")
        repo = CampaignMessageRepository(client)
        result = asyncio.run(repo.fetch_due_for_dispatch())
        self.assertEqual(result, [])


class TestScheduleStep(unittest.TestCase):
    def test_writes_step_variant_scheduled_at(self) -> None:
        client, table = _build_db([{"id": "msg-1"}])
        repo = CampaignMessageRepository(client)
        result = asyncio.run(
            repo.schedule_step(
                "msg-1",
                step_id="step-1",
                variant_id="variant-A",
                scheduled_at_iso="2026-05-26T09:00:00Z",
            )
        )
        self.assertTrue(result.matched)
        update_call = table.update.call_args.args[0]
        self.assertEqual(update_call["step_id"], "step-1")
        self.assertEqual(update_call["variant_id"], "variant-A")
        self.assertEqual(update_call["scheduled_at"], "2026-05-26T09:00:00Z")
        # State-machine: only flips pending rows.
        table.eq.assert_any_call("id", "msg-1")
        table.eq.assert_any_call("status", "pending")

    def test_missing_identifiers_no_op(self) -> None:
        client, _ = _build_db([{"id": "msg"}])
        repo = CampaignMessageRepository(client)
        for bad in (
            ("", "step", "variant"),
            ("msg", "", "variant"),
            ("msg", "step", ""),
        ):
            result = asyncio.run(
                repo.schedule_step(
                    bad[0],
                    step_id=bad[1],
                    variant_id=bad[2],
                    scheduled_at_iso="2026-05-26T09:00:00Z",
                )
            )
            self.assertFalse(result.matched)
            self.assertEqual(result.error, "missing identifiers")
        client.table.assert_not_called()

    def test_non_pending_row_returns_no_match(self) -> None:
        """Real PostgREST: WHERE id=X AND status='pending' on a row that's
        already in 'sent' matches 0 rows. The mock returns empty data;
        repo translates to MarkResult(matched=False)."""
        client, _ = _build_db([])
        repo = CampaignMessageRepository(client)
        result = asyncio.run(
            repo.schedule_step(
                "msg-already-sent",
                step_id="step-1",
                variant_id="variant-A",
                scheduled_at_iso="2026-05-26T09:00:00Z",
            )
        )
        self.assertFalse(result.matched)
        self.assertIsNone(result.error)  # not an error — just a no-op


if __name__ == "__main__":
    unittest.main()
