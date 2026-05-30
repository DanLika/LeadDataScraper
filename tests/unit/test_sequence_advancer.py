"""Unit tests for src/services/sequence_advancer.py.

Coverage:
- _sent advances when next step's branch != 'replied'
- _sent SKIPS advance when next step's branch == 'replied' (inverted gate)
- _replied advances when next step's branch == 'replied'
- _replied SKIPS advance when next step's branch != 'replied'
- Sequence complete (no step_index+1) → reason='sequence_complete'
- Missing sequence context (no sequence_id) → reason='missing_sequence_context'
- UNIQUE collision on duplicate _sent → reason='insert_skipped_or_duplicate'
- thread_with_prior=True → in_reply_to_message_id stamped
- scheduled_at bumped to next valid window when delay lands out-of-window
"""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

from src.services.sequence_advancer import advance_to_next_step


@dataclass
class _Step:
    id: str
    sequence_id: str
    step_index: int
    channel: str = "email"
    delay_days: int = 0
    delay_hours: int = 0
    thread_with_prior: bool = False
    branch_condition: str = "always"
    send_window_start: str = "09:00"
    send_window_end: str = "17:00"
    send_days: str = "mon,tue,wed,thu,fri"


def _mk_msg(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "msg-cur",
        "lead_unique_key": "lead-1",
        "campaign_id": "camp-1",
        "sequence_id": "seq-1",
        "step_id": "step-0",
        "provider_message_id": "instantly-msg-001",
    }
    base.update(overrides)
    return base


def _mk_step_repo(by_id: dict[str, _Step], by_index: dict[tuple, _Step]) -> MagicMock:
    repo = MagicMock()
    repo.get_by_id = AsyncMock(side_effect=lambda sid: by_id.get(sid))
    repo.fetch_many = AsyncMock(
        side_effect=lambda ids: {sid: by_id[sid] for sid in ids if sid in by_id}
    )
    repo.get_by_index = AsyncMock(
        side_effect=lambda seq_id, idx: by_index.get((seq_id, idx))
    )
    return repo


def _mk_msg_repo(insert_returns: Optional[dict] = None) -> MagicMock:
    repo = MagicMock()
    repo.insert_next_step_row = AsyncMock(return_value=insert_returns)
    return repo


class TestBranchGating(unittest.TestCase):
    def test_sent_advances_on_always_branch(self) -> None:
        cur = _Step(id="step-0", sequence_id="seq-1", step_index=0)
        nxt = _Step(
            id="step-1",
            sequence_id="seq-1",
            step_index=1,
            delay_days=3,
            branch_condition="always",
        )
        step_repo = _mk_step_repo({"step-0": cur}, {("seq-1", 1): nxt})
        msg_repo = _mk_msg_repo({"id": "msg-new"})

        result = asyncio.run(
            advance_to_next_step(
                current_message=_mk_msg(),
                step_repo=step_repo,
                message_repo=msg_repo,
                event_type="sent",
                sent_at=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
            )
        )
        self.assertTrue(result.advanced)
        self.assertEqual(result.next_step_id, "step-1")
        msg_repo.insert_next_step_row.assert_called_once()

    def test_sent_skips_when_next_is_replied_branch(self) -> None:
        """Inverted gate: a 'replied' branch step doesn't fire on _sent."""
        cur = _Step(id="step-0", sequence_id="seq-1", step_index=0)
        nxt = _Step(
            id="step-1-replied",
            sequence_id="seq-1",
            step_index=1,
            delay_days=1,
            branch_condition="replied",
        )
        step_repo = _mk_step_repo({"step-0": cur}, {("seq-1", 1): nxt})
        msg_repo = _mk_msg_repo({"id": "msg-new"})

        result = asyncio.run(
            advance_to_next_step(
                current_message=_mk_msg(),
                step_repo=step_repo,
                message_repo=msg_repo,
                event_type="sent",
            )
        )
        self.assertFalse(result.advanced)
        self.assertEqual(result.reason, "next_step_replied_only")
        msg_repo.insert_next_step_row.assert_not_called()

    def test_replied_advances_when_next_is_replied_branch(self) -> None:
        cur = _Step(id="step-0", sequence_id="seq-1", step_index=0)
        nxt = _Step(
            id="step-1-replied",
            sequence_id="seq-1",
            step_index=1,
            delay_hours=2,
            branch_condition="replied",
        )
        step_repo = _mk_step_repo({"step-0": cur}, {("seq-1", 1): nxt})
        msg_repo = _mk_msg_repo({"id": "msg-reply-branch"})

        result = asyncio.run(
            advance_to_next_step(
                current_message=_mk_msg(),
                step_repo=step_repo,
                message_repo=msg_repo,
                event_type="replied",
                sent_at=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
            )
        )
        self.assertTrue(result.advanced)

    def test_replied_skips_when_next_is_not_replied_branch(self) -> None:
        """A reply on a normal sequence doesn't advance the 'always' /
        'no_reply' branches — those were already scheduled by _sent."""
        cur = _Step(id="step-0", sequence_id="seq-1", step_index=0)
        nxt = _Step(
            id="step-1",
            sequence_id="seq-1",
            step_index=1,
            delay_days=3,
            branch_condition="always",
        )
        step_repo = _mk_step_repo({"step-0": cur}, {("seq-1", 1): nxt})
        msg_repo = _mk_msg_repo({"id": "msg-new"})

        result = asyncio.run(
            advance_to_next_step(
                current_message=_mk_msg(),
                step_repo=step_repo,
                message_repo=msg_repo,
                event_type="replied",
            )
        )
        self.assertFalse(result.advanced)
        self.assertEqual(result.reason, "next_step_not_replied_branch")


class TestSequenceComplete(unittest.TestCase):
    def test_no_next_step_short_circuits(self) -> None:
        cur = _Step(id="step-last", sequence_id="seq-1", step_index=3)
        step_repo = _mk_step_repo({"step-last": cur}, {})
        msg_repo = _mk_msg_repo()

        result = asyncio.run(
            advance_to_next_step(
                current_message=_mk_msg(step_id="step-last"),
                step_repo=step_repo,
                message_repo=msg_repo,
                event_type="sent",
            )
        )
        self.assertFalse(result.advanced)
        self.assertEqual(result.reason, "sequence_complete")
        msg_repo.insert_next_step_row.assert_not_called()


class TestMissingContext(unittest.TestCase):
    def test_missing_sequence_id_short_circuits(self) -> None:
        step_repo = MagicMock()
        msg_repo = MagicMock()
        result = asyncio.run(
            advance_to_next_step(
                current_message=_mk_msg(sequence_id=None),
                step_repo=step_repo,
                message_repo=msg_repo,
                event_type="sent",
            )
        )
        self.assertFalse(result.advanced)
        self.assertEqual(result.reason, "missing_sequence_context")

    def test_current_step_not_found_short_circuits(self) -> None:
        step_repo = _mk_step_repo({}, {})
        msg_repo = _mk_msg_repo()

        result = asyncio.run(
            advance_to_next_step(
                current_message=_mk_msg(step_id="missing-step"),
                step_repo=step_repo,
                message_repo=msg_repo,
                event_type="sent",
            )
        )
        self.assertFalse(result.advanced)
        self.assertEqual(result.reason, "current_step_not_found")
        msg_repo.insert_next_step_row.assert_not_called()

    def test_fallback_to_fetch_many_when_get_by_id_missing_or_none(self) -> None:
        cur = _Step(id="step-0", sequence_id="seq-1", step_index=0)
        nxt = _Step(
            id="step-1",
            sequence_id="seq-1",
            step_index=1,
            delay_days=3,
            branch_condition="always",
        )

        step_repo = _mk_step_repo({"step-0": cur}, {("seq-1", 1): nxt})
        # Force get_by_id to fail/return None so it falls back to fetch_many
        step_repo.get_by_id = AsyncMock(return_value=None)

        msg_repo = _mk_msg_repo({"id": "msg-new"})

        result = asyncio.run(
            advance_to_next_step(
                current_message=_mk_msg(),
                step_repo=step_repo,
                message_repo=msg_repo,
                event_type="sent",
            )
        )
        self.assertTrue(result.advanced)
        self.assertEqual(result.next_step_id, "step-1")
        step_repo.fetch_many.assert_called_once_with(["step-0"])
        msg_repo.insert_next_step_row.assert_called_once()


class TestIdempotentInsert(unittest.TestCase):
    def test_unique_collision_returns_skipped(self) -> None:
        """Duplicate _sent webhook → repo's UNIQUE collision → repo
        returns None → advancer surfaces reason='insert_skipped_or_duplicate'."""
        cur = _Step(id="step-0", sequence_id="seq-1", step_index=0)
        nxt = _Step(
            id="step-1",
            sequence_id="seq-1",
            step_index=1,
            delay_days=3,
            branch_condition="always",
        )
        step_repo = _mk_step_repo({"step-0": cur}, {("seq-1", 1): nxt})
        msg_repo = _mk_msg_repo(insert_returns=None)  # simulates UNIQUE skip

        result = asyncio.run(
            advance_to_next_step(
                current_message=_mk_msg(),
                step_repo=step_repo,
                message_repo=msg_repo,
                event_type="sent",
                sent_at=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
            )
        )
        self.assertFalse(result.advanced)
        self.assertEqual(result.reason, "insert_skipped_or_duplicate")
        # Insert WAS attempted — important for the idempotency contract.
        msg_repo.insert_next_step_row.assert_called_once()


class TestThreadContinuation(unittest.TestCase):
    def test_in_reply_to_passed_when_thread_with_prior(self) -> None:
        cur = _Step(id="step-0", sequence_id="seq-1", step_index=0)
        nxt = _Step(
            id="step-1",
            sequence_id="seq-1",
            step_index=1,
            delay_days=3,
            thread_with_prior=True,
            branch_condition="always",
        )
        step_repo = _mk_step_repo({"step-0": cur}, {("seq-1", 1): nxt})
        msg_repo = _mk_msg_repo({"id": "msg-new"})

        asyncio.run(
            advance_to_next_step(
                current_message=_mk_msg(provider_message_id="instantly-xxx"),
                step_repo=step_repo,
                message_repo=msg_repo,
                event_type="sent",
                sent_at=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
            )
        )
        call_kwargs = msg_repo.insert_next_step_row.call_args.kwargs
        self.assertEqual(call_kwargs["in_reply_to_message_id"], "instantly-xxx")

    def test_no_in_reply_to_when_thread_disabled(self) -> None:
        cur = _Step(id="step-0", sequence_id="seq-1", step_index=0)
        nxt = _Step(
            id="step-1",
            sequence_id="seq-1",
            step_index=1,
            delay_days=3,
            thread_with_prior=False,
            branch_condition="always",
        )
        step_repo = _mk_step_repo({"step-0": cur}, {("seq-1", 1): nxt})
        msg_repo = _mk_msg_repo({"id": "msg-new"})

        asyncio.run(
            advance_to_next_step(
                current_message=_mk_msg(),
                step_repo=step_repo,
                message_repo=msg_repo,
                event_type="sent",
                sent_at=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
            )
        )
        call_kwargs = msg_repo.insert_next_step_row.call_args.kwargs
        self.assertIsNone(call_kwargs["in_reply_to_message_id"])


class TestWindowBump(unittest.TestCase):
    def test_scheduled_at_bumped_when_delay_lands_outside_window(self) -> None:
        """sent_at = Fri 16:00 UTC; delay = 6h → would land Fri 22:00.
        Mon-Fri 09-17 window → push to Mon 09:00 UTC."""
        cur = _Step(id="step-0", sequence_id="seq-1", step_index=0)
        nxt = _Step(
            id="step-1",
            sequence_id="seq-1",
            step_index=1,
            delay_hours=6,
            branch_condition="always",
        )
        step_repo = _mk_step_repo({"step-0": cur}, {("seq-1", 1): nxt})
        msg_repo = _mk_msg_repo({"id": "msg-new"})

        # 2026-05-29 is Friday.
        result = asyncio.run(
            advance_to_next_step(
                current_message=_mk_msg(),
                step_repo=step_repo,
                message_repo=msg_repo,
                event_type="sent",
                sent_at=datetime(2026, 5, 29, 16, 0, tzinfo=timezone.utc),
            )
        )
        self.assertTrue(result.advanced)
        # The scheduled_at_iso passed to repo should be Mon 09:00 UTC.
        call_kwargs = msg_repo.insert_next_step_row.call_args.kwargs
        sched = call_kwargs["scheduled_at_iso"]
        # Parse and assert it's Monday 09:00 UTC.
        parsed = datetime.fromisoformat(sched)
        self.assertEqual(parsed.weekday(), 0)  # Monday
        self.assertEqual(parsed.hour, 9)
        self.assertEqual(parsed.minute, 0)


if __name__ == "__main__":
    unittest.main()
