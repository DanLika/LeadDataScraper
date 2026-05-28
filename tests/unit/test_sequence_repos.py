"""Unit tests for the Phase 15.1 sequence repository layer.

Three repos:
- SequenceRepository: create / list_active / update_status idempotent
- SequenceStepRepository: create / list / get_by_index; UNIQUE collision
- SequenceVariantRepository: label format validation + UNIQUE collision

All tests use a chainable supabase-py mock; no live DB. Predicate
assertions verify the chain shape so future-refactors don't silently
drop a filter.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import MagicMock

from src.repositories.sequence_repo import (
    Sequence,
    SequenceRepository,
)
from src.repositories.sequence_step_repo import (
    SequenceStep,
    SequenceStepRepository,
)
from src.repositories.sequence_variant_repo import (
    SequenceVariant,
    SequenceVariantRepository,
)


def _build_db(rows: list[dict[str, Any]] | None = None) -> tuple[Any, MagicMock]:
    table = MagicMock(name="table")
    table.select.return_value = table
    table.insert.return_value = table
    table.update.return_value = table
    table.eq.return_value = table
    table.neq.return_value = table
    table.in_.return_value = table
    table.is_.return_value = table
    table.lte.return_value = table
    table.limit.return_value = table
    table.order.return_value = table

    class _Result:
        def __init__(self, data: list[dict[str, Any]]) -> None:
            self.data = data

    table.execute.return_value = _Result(rows or [])
    client = MagicMock(name="client")
    client.table.return_value = table
    return client, table


# ---------------------------------------------------------------------------
# SequenceRepository
# ---------------------------------------------------------------------------


class TestSequenceRepo(unittest.TestCase):
    def test_list_active_for_campaign_filters_status_active(self) -> None:
        rows = [
            {
                "id": "s-1",
                "campaign_id": "c-1",
                "name": "Seq A",
                "status": "active",
                "created_at": "2026-05-25T00:00:00Z",
                "updated_at": "2026-05-25T00:00:00Z",
            },
        ]
        client, table = _build_db(rows)
        repo = SequenceRepository(client)
        result = asyncio.run(repo.list_active_for_campaign("c-1"))
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], Sequence)
        # Predicate chain.
        table.eq.assert_any_call("campaign_id", "c-1")
        table.eq.assert_any_call("status", "active")
        table.order.assert_called_with("created_at", desc=False)

    def test_empty_campaign_id_returns_empty_list(self) -> None:
        client, _ = _build_db([{"id": "x"}])
        repo = SequenceRepository(client)
        self.assertEqual(asyncio.run(repo.list_active_for_campaign("")), [])
        client.table.assert_not_called()

    def test_get_by_id_returns_single(self) -> None:
        rows = [
            {
                "id": "s-2",
                "campaign_id": "c-1",
                "name": "N",
                "status": "draft",
                "created_at": "",
                "updated_at": "",
            }
        ]
        client, _ = _build_db(rows)
        repo = SequenceRepository(client)
        seq = asyncio.run(repo.get_by_id("s-2"))
        self.assertIsNotNone(seq)
        self.assertEqual(seq.id, "s-2")

    def test_create_returns_inserted_row(self) -> None:
        rows = [
            {
                "id": "s-3",
                "campaign_id": "c-1",
                "name": "New",
                "status": "draft",
                "created_at": "",
                "updated_at": "",
            }
        ]
        client, table = _build_db(rows)
        repo = SequenceRepository(client)
        seq = asyncio.run(repo.create("c-1", "New"))
        self.assertEqual(seq.name, "New")
        sent = table.insert.call_args.args[0]
        self.assertEqual(sent["campaign_id"], "c-1")
        self.assertEqual(sent["status"], "draft")

    def test_update_status_idempotent_predicate(self) -> None:
        """Re-applying the same status is a no-op via .neq("status", new)."""
        client, table = _build_db([{"id": "s-1"}])
        repo = SequenceRepository(client)
        result = asyncio.run(repo.update_status("s-1", "active"))
        self.assertTrue(result)
        table.eq.assert_any_call("id", "s-1")
        table.neq.assert_called_with("status", "active")


# ---------------------------------------------------------------------------
# SequenceStepRepository
# ---------------------------------------------------------------------------


class TestSequenceStepRepo(unittest.TestCase):
    def test_list_for_sequence_orders_by_step_index(self) -> None:
        rows = [
            {
                "id": "step-0",
                "sequence_id": "s-1",
                "step_index": 0,
                "channel": "email",
                "delay_days": 0,
                "delay_hours": 0,
                "thread_with_prior": False,
                "branch_condition": "always",
                "send_window_start": "09:00:00",
                "send_window_end": "17:00:00",
                "send_days": "mon,tue,wed,thu,fri",
                "created_at": "",
            },
        ]
        client, table = _build_db(rows)
        repo = SequenceStepRepository(client)
        result = asyncio.run(repo.list_for_sequence("s-1"))
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], SequenceStep)
        table.order.assert_called_with("step_index", desc=False)

    def test_get_by_index(self) -> None:
        rows = [
            {
                "id": "step-2",
                "sequence_id": "s-1",
                "step_index": 2,
                "channel": "email",
                "delay_days": 7,
                "delay_hours": 0,
                "thread_with_prior": True,
                "branch_condition": "no_reply",
                "send_window_start": "09:00:00",
                "send_window_end": "17:00:00",
                "send_days": "mon,tue,wed,thu,fri",
                "created_at": "",
            },
        ]
        client, _ = _build_db(rows)
        repo = SequenceStepRepository(client)
        step = asyncio.run(repo.get_by_index("s-1", 2))
        self.assertIsNotNone(step)
        self.assertEqual(step.step_index, 2)
        self.assertTrue(step.thread_with_prior)
        self.assertEqual(step.branch_condition, "no_reply")

    def test_negative_step_index_rejected_client_side(self) -> None:
        client, _ = _build_db([{"id": "x"}])
        repo = SequenceStepRepository(client)
        self.assertIsNone(asyncio.run(repo.get_by_index("s-1", -1)))
        client.table.assert_not_called()

    def test_create_unique_collision_returns_none(self) -> None:
        class _Dup(Exception):
            code = "23505"

        client, table = _build_db([])
        table.execute.side_effect = _Dup("duplicate key value")
        repo = SequenceStepRepository(client)
        result = asyncio.run(repo.create("s-1", 0))
        self.assertIsNone(result)

    def test_create_with_full_payload(self) -> None:
        rows = [
            {
                "id": "step-new",
                "sequence_id": "s-1",
                "step_index": 1,
                "channel": "email",
                "delay_days": 3,
                "delay_hours": 6,
                "thread_with_prior": True,
                "branch_condition": "no_reply",
                "send_window_start": "08:00:00",
                "send_window_end": "18:00:00",
                "send_days": "mon,wed,fri",
                "created_at": "",
            },
        ]
        client, table = _build_db(rows)
        repo = SequenceStepRepository(client)
        asyncio.run(
            repo.create(
                "s-1",
                1,
                channel="email",
                delay_days=3,
                delay_hours=6,
                thread_with_prior=True,
                branch_condition="no_reply",
                send_window_start="08:00",
                send_window_end="18:00",
                send_days="mon,wed,fri",
            )
        )
        sent = table.insert.call_args.args[0]
        self.assertEqual(sent["delay_days"], 3)
        self.assertEqual(sent["thread_with_prior"], True)
        self.assertEqual(sent["branch_condition"], "no_reply")
        self.assertEqual(sent["send_days"], "mon,wed,fri")


# ---------------------------------------------------------------------------
# SequenceVariantRepository
# ---------------------------------------------------------------------------


class TestSequenceVariantRepo(unittest.TestCase):
    def test_list_for_step_orders_by_label(self) -> None:
        rows = [
            {
                "id": "v-A",
                "step_id": "step-1",
                "variant_label": "A",
                "subject_template": "S",
                "body_template": "B",
                "weight": 60,
                "ai_model_used": None,
                "ai_prompt_version": None,
                "created_at": "",
            },
        ]
        client, table = _build_db(rows)
        repo = SequenceVariantRepository(client)
        result = asyncio.run(repo.list_for_step("step-1"))
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], SequenceVariant)
        table.order.assert_called_with("variant_label", desc=False)

    def test_invalid_label_format_rejected_client_side(self) -> None:
        client, _ = _build_db([{"id": "x"}])
        repo = SequenceVariantRepository(client)
        # Lowercase, multi-char, digit — all rejected.
        self.assertIsNone(asyncio.run(repo.create("step-1", "a", "body")))
        self.assertIsNone(asyncio.run(repo.create("step-1", "AB", "body")))
        self.assertIsNone(asyncio.run(repo.create("step-1", "1", "body")))
        client.table.assert_not_called()

    def test_non_positive_weight_rejected_client_side(self) -> None:
        client, _ = _build_db([{"id": "x"}])
        repo = SequenceVariantRepository(client)
        self.assertIsNone(asyncio.run(repo.create("step-1", "A", "body", weight=0)))
        self.assertIsNone(asyncio.run(repo.create("step-1", "A", "body", weight=-5)))
        client.table.assert_not_called()

    def test_unique_collision_returns_none(self) -> None:
        class _Dup(Exception):
            code = "23505"

        client, table = _build_db([])
        table.execute.side_effect = _Dup("duplicate key value")
        repo = SequenceVariantRepository(client)
        self.assertIsNone(asyncio.run(repo.create("step-1", "A", "body")))

    def test_create_persists_ai_metadata(self) -> None:
        rows = [
            {
                "id": "v-B",
                "step_id": "step-1",
                "variant_label": "B",
                "subject_template": None,
                "body_template": "Hi",
                "weight": 50,
                "ai_model_used": "gemini-flash-latest",
                "ai_prompt_version": "v3",
                "created_at": "",
            },
        ]
        client, table = _build_db(rows)
        repo = SequenceVariantRepository(client)
        v = asyncio.run(
            repo.create(
                "step-1",
                "B",
                "Hi",
                ai_model_used="gemini-flash-latest",
                ai_prompt_version="v3",
            )
        )
        sent = table.insert.call_args.args[0]
        self.assertEqual(sent["ai_model_used"], "gemini-flash-latest")
        self.assertEqual(sent["ai_prompt_version"], "v3")
        self.assertEqual(v.ai_model_used, "gemini-flash-latest")


if __name__ == "__main__":
    unittest.main()
