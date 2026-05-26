"""Unit tests for src/repositories/campaign_message_repo.py.

Idempotent UPDATE patterns:
- mark_sent: first-hit-wins via .is_("provider_message_id", "null")
- mark_bounced: state-machine .in_("status", ["pending", "sent"])
- mark_unsubscribed: .in_("status", ["pending", "sent", "replied"])
- mark_replied: .in_("status", ["sent"])
- mark_send_failed: .eq("status", "pending") — dispatcher-only path

Race coverage:
- Webhook arrives before campaign_messages row exists (mark_sent → no-op)
- Bounce before email_sent (provider_message_id is NULL, matches 0 rows)
- Duplicate email_sent (replay) → first-hit-wins
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
    """Build a recording supabase-py mock.

    ``execute()`` returns an object whose ``.data`` is ``rows`` — pass
    a non-empty list to simulate "predicate matched N rows", empty to
    simulate "no rows matched" (the idempotent-replay case)."""
    table = MagicMock(name="table")
    table.update.return_value = table
    table.eq.return_value = table
    table.in_.return_value = table
    table.is_.return_value = table
    table.select.return_value = table
    table.limit.return_value = table

    class _Result:
        def __init__(self, data: list[dict[str, Any]]) -> None:
            self.data = data

    table.execute.return_value = _Result(rows or [])
    client = MagicMock(name="client")
    client.table.return_value = table
    return client, table


# ---------------------------------------------------------------------------
# mark_sent — first-hit-wins
# ---------------------------------------------------------------------------


class TestMarkSent(unittest.TestCase):
    def test_first_hit_writes_provider_message_id(self) -> None:
        client, table = _build_db([{"id": "msg-uuid"}])
        repo = CampaignMessageRepository(client)
        result = asyncio.run(repo.mark_sent(
            "msg-uuid", "instantly-msg-001", sent_at_iso="2026-05-25T10:00:00Z",
        ))
        self.assertTrue(result.matched)
        # Verify the UPDATE payload + predicate chain.
        update_call = table.update.call_args.args[0]
        self.assertEqual(update_call["provider_message_id"], "instantly-msg-001")
        self.assertEqual(update_call["status"], "sent")
        self.assertEqual(update_call["sent_at"], "2026-05-25T10:00:00Z")
        # Predicate chain: .eq("id", X).is_("provider_message_id", "null")
        table.eq.assert_called_with("id", "msg-uuid")
        table.is_.assert_called_with("provider_message_id", "null")

    def test_replay_is_noop(self) -> None:
        """Predicate `provider_message_id IS NULL` matches 0 rows on replay."""
        client, _ = _build_db([])  # empty data = no rows matched
        repo = CampaignMessageRepository(client)
        result = asyncio.run(repo.mark_sent(
            "msg-uuid", "instantly-msg-001", sent_at_iso="2026-05-25T10:00:00Z",
        ))
        self.assertFalse(result.matched)
        self.assertIsNone(result.error)

    def test_missing_identifiers_returns_no_op(self) -> None:
        client, table = _build_db([{"id": "msg-uuid"}])
        repo = CampaignMessageRepository(client)
        # Empty lds_message_id
        result = asyncio.run(repo.mark_sent("", "x", sent_at_iso="2026-05-25T10:00:00Z"))
        self.assertFalse(result.matched)
        client.table.assert_not_called()
        # Empty provider_message_id
        result = asyncio.run(repo.mark_sent("msg", "", sent_at_iso="2026-05-25T10:00:00Z"))
        self.assertFalse(result.matched)

    def test_db_exception_returns_error(self) -> None:
        client, table = _build_db([])
        table.execute.side_effect = RuntimeError("connection refused")
        repo = CampaignMessageRepository(client)
        result = asyncio.run(repo.mark_sent(
            "msg-uuid", "instantly-msg-001", sent_at_iso="2026-05-25T10:00:00Z",
        ))
        self.assertFalse(result.matched)
        self.assertEqual(result.error, "RuntimeError")


# ---------------------------------------------------------------------------
# mark_bounced — state machine pending|sent → bounced
# ---------------------------------------------------------------------------


class TestMarkBounced(unittest.TestCase):
    def test_writes_bounced_status_and_reason(self) -> None:
        client, table = _build_db([{"id": "msg-uuid"}])
        repo = CampaignMessageRepository(client)
        result = asyncio.run(repo.mark_bounced(
            "instantly-msg-bounce-1", bounce_reason="550 mailbox not found",
        ))
        self.assertTrue(result.matched)
        update_call = table.update.call_args.args[0]
        self.assertEqual(update_call["status"], "bounced")
        self.assertEqual(update_call["bounce_reason"], "550 mailbox not found")
        # State-machine: only fires from pending/sent.
        table.in_.assert_called_with("status", ["pending", "sent"])

    def test_truncates_long_bounce_reason(self) -> None:
        client, table = _build_db([{"id": "msg-uuid"}])
        repo = CampaignMessageRepository(client)
        asyncio.run(repo.mark_bounced("msg", bounce_reason="x" * 500))
        update_call = table.update.call_args.args[0]
        self.assertLessEqual(len(update_call["bounce_reason"]), 200)

    def test_empty_bounce_reason_becomes_null(self) -> None:
        client, table = _build_db([{"id": "msg-uuid"}])
        repo = CampaignMessageRepository(client)
        asyncio.run(repo.mark_bounced("msg", bounce_reason=""))
        update_call = table.update.call_args.args[0]
        self.assertIsNone(update_call["bounce_reason"])

    def test_race_bounce_before_sent_returns_no_match(self) -> None:
        """If email_sent webhook hasn't fired yet, provider_message_id is NULL
        in the row and the bounce UPDATE matches zero rows. Documented in
        the repo docstring as acceptable degraded state — suppression
        INSERT still fires via recipient_email upstream."""
        client, _ = _build_db([])
        repo = CampaignMessageRepository(client)
        result = asyncio.run(repo.mark_bounced("never-stamped-msg-id"))
        self.assertFalse(result.matched)


# ---------------------------------------------------------------------------
# mark_unsubscribed — state machine
# ---------------------------------------------------------------------------


class TestMarkUnsubscribed(unittest.TestCase):
    def test_state_machine_predicate_includes_replied(self) -> None:
        client, table = _build_db([{"id": "msg-uuid"}])
        repo = CampaignMessageRepository(client)
        asyncio.run(repo.mark_unsubscribed("msg-1"))
        table.in_.assert_called_with("status", ["pending", "sent", "replied"])
        update_call = table.update.call_args.args[0]
        self.assertEqual(update_call["status"], "unsubscribed")


# ---------------------------------------------------------------------------
# mark_replied — strict sent-only transition
# ---------------------------------------------------------------------------


class TestMarkReplied(unittest.TestCase):
    def test_state_machine_predicate_is_sent_only(self) -> None:
        client, table = _build_db([{"id": "msg-uuid"}])
        repo = CampaignMessageRepository(client)
        asyncio.run(repo.mark_replied("msg-1"))
        table.in_.assert_called_with("status", ["sent"])
        update_call = table.update.call_args.args[0]
        self.assertEqual(update_call["status"], "replied")


# ---------------------------------------------------------------------------
# mark_send_failed — dispatcher-side failure path
# ---------------------------------------------------------------------------


class TestMarkSendFailed(unittest.TestCase):
    def test_pending_only_transition(self) -> None:
        client, table = _build_db([{"id": "msg-uuid"}])
        repo = CampaignMessageRepository(client)
        result = asyncio.run(repo.mark_send_failed(
            "msg-uuid", error="rate_limit",
        ))
        self.assertTrue(result.matched)
        # State machine: only flip pending → bounced (not sent → bounced).
        table.eq.assert_any_call("status", "pending")
        update_call = table.update.call_args.args[0]
        self.assertEqual(update_call["status"], "bounced")
        self.assertEqual(update_call["bounce_reason"], "send_failed: rate_limit")

    def test_truncates_error_message(self) -> None:
        client, table = _build_db([{"id": "msg-uuid"}])
        repo = CampaignMessageRepository(client)
        asyncio.run(repo.mark_send_failed("msg", error="x" * 500))
        update_call = table.update.call_args.args[0]
        self.assertLessEqual(len(update_call["bounce_reason"]), 200)


if __name__ == "__main__":
    unittest.main()
