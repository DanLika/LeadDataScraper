"""Phase 15.4 additions to CampaignMessageRepository.

- cancel_pending_steps_for_lead(lead_uk, sequence_id=None, reason=...)
  * per-sequence cancel (sequence_id set) — predicate carries seq filter
  * cross-sequence cancel (sequence_id=None) — no seq filter
  * predicate ALWAYS includes status='pending' (known-race documented)
- insert_next_step_row(...)
  * happy path payload + status='pending'
  * UNIQUE collision (23505) returns None — idempotent _sent replay
- _is_unique_violation_exc: code attr + message substring
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import MagicMock

from src.repositories.campaign_message_repo import (
    CampaignMessageRepository,
    _is_unique_violation_exc,
)


def _build_db(
    update_returns: list[dict] | None = None,
    insert_returns: list[dict] | None = None,
    insert_raises: Exception | None = None,
) -> tuple[Any, MagicMock, dict]:
    table = MagicMock(name="table")
    captures: dict[str, Any] = {"updates": [], "inserts": []}
    table._set: dict[str, Any] = {}
    table._where: dict[str, Any] = {}

    table.select.return_value = table
    table.update.side_effect = lambda values, t=table: (
        setattr(t, "_set", dict(values)) or t
    )
    table.insert.side_effect = lambda values, t=table: (
        captures["inserts"].append(dict(values)) or t
    )
    table.eq.side_effect = lambda col, val, t=table: t._where.__setitem__(col, val) or t
    table.in_.return_value = table
    table.lt.return_value = table
    table.lte.return_value = table
    table.limit.return_value = table
    table.order.return_value = table

    def execute():
        if insert_raises and captures["inserts"]:
            raise insert_raises
        if table._set:
            captures["updates"].append((dict(table._set), dict(table._where)))
            table._set = {}
            table._where = {}
            return MagicMock(data=list(update_returns or []))
        if captures["inserts"]:
            return MagicMock(data=list(insert_returns or []))
        return MagicMock(data=[])

    table.execute.side_effect = execute
    client = MagicMock(name="client")
    client.table.return_value = table
    return client, table, captures


class TestCancelPendingSteps(unittest.TestCase):
    def test_per_sequence_cancel_includes_seq_filter(self) -> None:
        cancelled = [{"id": "m1"}, {"id": "m2"}]
        client, _, captures = _build_db(update_returns=cancelled)
        repo = CampaignMessageRepository(client)
        result = asyncio.run(
            repo.cancel_pending_steps_for_lead(
                "lead-1",
                sequence_id="seq-A",
                reason="bounce",
            )
        )
        self.assertEqual(result, 2)
        set_clause, where = captures["updates"][0]
        self.assertEqual(set_clause["status"], "cancelled")
        self.assertEqual(set_clause["bounce_reason"], "cancelled:bounce")
        # Predicate scope: lead + pending + sequence.
        self.assertEqual(where["lead_unique_key"], "lead-1")
        self.assertEqual(where["status"], "pending")
        self.assertEqual(where["sequence_id"], "seq-A")

    def test_cross_sequence_cancel_omits_seq_filter(self) -> None:
        cancelled = [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]
        client, _, captures = _build_db(update_returns=cancelled)
        repo = CampaignMessageRepository(client)
        result = asyncio.run(
            repo.cancel_pending_steps_for_lead(
                "lead-1",
                sequence_id=None,
                reason="unsubscribed_cross_channel",
            )
        )
        self.assertEqual(result, 3)
        _, where = captures["updates"][0]
        # CROSS-SEQUENCE: lead + status only, no sequence_id filter.
        self.assertEqual(where["lead_unique_key"], "lead-1")
        self.assertEqual(where["status"], "pending")
        self.assertNotIn("sequence_id", where)

    def test_empty_lead_short_circuits(self) -> None:
        client, _, captures = _build_db()
        repo = CampaignMessageRepository(client)
        result = asyncio.run(repo.cancel_pending_steps_for_lead(""))
        self.assertEqual(result, 0)
        self.assertEqual(len(captures["updates"]), 0)

    def test_known_race_dispatching_status_NOT_cancelled(self) -> None:
        """Documented race (PR body + module docstring): rows already in
        'dispatching' are EXCLUDED by the status='pending' predicate.
        This test pins the SQL predicate so a future "fix" doesn't
        relax the filter without an explicit decision."""
        client, _, captures = _build_db(update_returns=[])
        repo = CampaignMessageRepository(client)
        asyncio.run(repo.cancel_pending_steps_for_lead("lead-1"))
        _, where = captures["updates"][0]
        # MUST be status='pending', NOT status IN ('pending','dispatching').
        self.assertEqual(where["status"], "pending")


class TestInsertNextStepRow(unittest.TestCase):
    def test_happy_path_inserts_pending_row(self) -> None:
        new_row = {"id": "msg-new"}
        client, _, captures = _build_db(insert_returns=[new_row])
        repo = CampaignMessageRepository(client)
        result = asyncio.run(
            repo.insert_next_step_row(
                lead_unique_key="lead-1",
                campaign_id="camp-1",
                sequence_id="seq-1",
                step_id="step-1",
                channel="email",
                scheduled_at_iso="2026-05-29T09:00:00+00:00",
                in_reply_to_message_id="instantly-prior-001",
            )
        )
        self.assertEqual(result, {"id": "msg-new"})
        payload = captures["inserts"][0]
        self.assertEqual(payload["lead_unique_key"], "lead-1")
        self.assertEqual(payload["sequence_id"], "seq-1")
        self.assertEqual(payload["step_id"], "step-1")
        self.assertEqual(payload["status"], "pending")
        self.assertEqual(payload["scheduled_at"], "2026-05-29T09:00:00+00:00")
        self.assertEqual(payload["in_reply_to_message_id"], "instantly-prior-001")

    def test_unique_collision_returns_none(self) -> None:
        class _Dup(Exception):
            code = "23505"

        client, _, _ = _build_db(insert_raises=_Dup("duplicate key value"))
        repo = CampaignMessageRepository(client)
        result = asyncio.run(
            repo.insert_next_step_row(
                lead_unique_key="lead-1",
                campaign_id="camp-1",
                sequence_id="seq-1",
                step_id="step-1",
                channel="email",
                scheduled_at_iso="2026-05-29T09:00:00+00:00",
            )
        )
        self.assertIsNone(result)

    def test_missing_identifier_short_circuits(self) -> None:
        client, _, captures = _build_db()
        repo = CampaignMessageRepository(client)
        for bad in (
            ("", "camp-1", "seq-1", "step-1"),
            ("lead-1", "", "seq-1", "step-1"),
            ("lead-1", "camp-1", "", "step-1"),
            ("lead-1", "camp-1", "seq-1", ""),
        ):
            result = asyncio.run(
                repo.insert_next_step_row(
                    lead_unique_key=bad[0],
                    campaign_id=bad[1],
                    sequence_id=bad[2],
                    step_id=bad[3],
                    channel="email",
                    scheduled_at_iso="2026-05-29T09:00:00+00:00",
                )
            )
            self.assertIsNone(result)
        self.assertEqual(len(captures["inserts"]), 0)

    def test_none_in_reply_to_stripped_from_payload(self) -> None:
        client, _, captures = _build_db(insert_returns=[{"id": "msg-new"}])
        repo = CampaignMessageRepository(client)
        asyncio.run(
            repo.insert_next_step_row(
                lead_unique_key="lead-1",
                campaign_id="camp-1",
                sequence_id="seq-1",
                step_id="step-1",
                channel="email",
                scheduled_at_iso="2026-05-29T09:00:00+00:00",
                in_reply_to_message_id=None,
            )
        )
        payload = captures["inserts"][0]
        # None stripped so DB default applies (DB default is NULL for
        # this col, but stripping keeps the payload size down + makes
        # tracking_id default fire).
        self.assertNotIn("in_reply_to_message_id", payload)


class TestUniqueViolationDetector(unittest.TestCase):
    def test_code_attr(self) -> None:
        class _E(Exception):
            code = "23505"

        self.assertTrue(_is_unique_violation_exc(_E("x")))

    def test_message_substring(self) -> None:
        self.assertTrue(
            _is_unique_violation_exc(
                Exception("postgrest 23505 duplicate key value"),
            )
        )

    def test_unrelated_error(self) -> None:
        self.assertFalse(
            _is_unique_violation_exc(
                Exception("connection refused"),
            )
        )


if __name__ == "__main__":
    unittest.main()
