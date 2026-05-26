"""SequenceStepRepository — PostgREST I/O for ``public.sequence_steps``.

Phase 15.1 — ordered (sequence_id, step_index) units inside a sequence.
The dispatcher (Phase 15.2) reads steps to compute the next message
``scheduled_at``; the webhook handler (Phase 15.4) reads steps via the
``advance_to_next_step`` flow to find the next step after a transition.

UNIQUE (sequence_id, step_index) is enforced at the DB level
(``sequence_steps_unique_index``); the repo's ``create`` returns None
on the duplicate path so the caller can recover (e.g. retry with a
different index) without seeing a 500.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

# Mirrors sequence_steps_channel_allowed.
StepChannel = Literal["email", "linkedin"]
# Mirrors sequence_steps_branch_allowed.
BranchCondition = Literal[
    "always", "no_reply", "no_open", "connection_accepted", "replied",
]


@dataclass(frozen=True)
class SequenceStep:
    """Read-only view of a sequence_steps row."""

    id: str
    sequence_id: str
    step_index: int
    channel: StepChannel
    delay_days: int
    delay_hours: int
    thread_with_prior: bool
    branch_condition: BranchCondition
    send_window_start: str  # HH:MM:SS as PostgREST returns TIME
    send_window_end: str
    send_days: str  # comma-separated mon|tue|... tokens
    created_at: str


def _row_to_step(row: dict[str, Any]) -> SequenceStep:
    return SequenceStep(
        id=row["id"],
        sequence_id=row["sequence_id"],
        step_index=int(row["step_index"]),
        channel=row["channel"],
        delay_days=int(row.get("delay_days") or 0),
        delay_hours=int(row.get("delay_hours") or 0),
        thread_with_prior=bool(row.get("thread_with_prior")),
        branch_condition=row.get("branch_condition") or "always",
        send_window_start=str(row.get("send_window_start") or "09:00:00"),
        send_window_end=str(row.get("send_window_end") or "17:00:00"),
        send_days=str(row.get("send_days") or "mon,tue,wed,thu,fri"),
        created_at=row.get("created_at") or "",
    )


class SequenceStepRepository:
    """PostgREST adapter for ``public.sequence_steps``."""

    TABLE_NAME = "sequence_steps"

    def __init__(self, db: Any) -> None:
        self._db = db

    async def list_for_sequence(self, sequence_id: str) -> list[SequenceStep]:
        """All steps for one sequence, ordered by step_index ascending.

        Backed by ``idx_sequence_steps_lookup`` (sequence_id, step_index).
        """
        if not self._db or not sequence_id:
            return []
        rows = await asyncio.to_thread(
            lambda: (
                self._db.table(self.TABLE_NAME)
                .select("*")
                .eq("sequence_id", sequence_id)
                .order("step_index", desc=False)
                .execute()
            )
        )
        return [_row_to_step(r) for r in (getattr(rows, "data", None) or [])]

    async def get_by_index(
        self,
        sequence_id: str,
        step_index: int,
    ) -> Optional[SequenceStep]:
        """Lookup by (sequence_id, step_index) — the natural key.

        Used by ``advance_to_next_step`` (Phase 15.4) to find the next
        step after a transition. Returns None if no step at that index
        (sequence complete).
        """
        if not self._db or not sequence_id or step_index < 0:
            return None
        rows = await asyncio.to_thread(
            lambda: (
                self._db.table(self.TABLE_NAME)
                .select("*")
                .eq("sequence_id", sequence_id)
                .eq("step_index", step_index)
                .limit(1)
                .execute()
            )
        )
        data = getattr(rows, "data", None) or []
        return _row_to_step(data[0]) if data else None

    async def create(
        self,
        sequence_id: str,
        step_index: int,
        *,
        channel: StepChannel = "email",
        delay_days: int = 0,
        delay_hours: int = 0,
        thread_with_prior: bool = False,
        branch_condition: BranchCondition = "always",
        send_window_start: str = "09:00",
        send_window_end: str = "17:00",
        send_days: str = "mon,tue,wed,thu,fri",
    ) -> Optional[SequenceStep]:
        """Insert one step. Returns None on UNIQUE collision (same
        (sequence_id, step_index)) — caller retries with a fresh index
        or surfaces a UI error. Other DB errors are logged + None'd
        to keep the boundary uniform."""
        if not self._db or not sequence_id or step_index < 0:
            return None
        try:
            res = await asyncio.to_thread(
                lambda: (
                    self._db.table(self.TABLE_NAME)
                    .insert({
                        "sequence_id": sequence_id,
                        "step_index": step_index,
                        "channel": channel,
                        "delay_days": delay_days,
                        "delay_hours": delay_hours,
                        "thread_with_prior": thread_with_prior,
                        "branch_condition": branch_condition,
                        "send_window_start": send_window_start,
                        "send_window_end": send_window_end,
                        "send_days": send_days,
                    })
                    .execute()
                )
            )
        except Exception as exc:  # noqa: BLE001 — narrow inline
            if _is_unique_violation(exc):
                logger.info(
                    "SequenceStepRepository.create UNIQUE collision (%s, %d)",
                    sequence_id, step_index,
                )
                return None
            logger.exception("SequenceStepRepository.create failed")
            return None
        data = getattr(res, "data", None) or []
        return _row_to_step(data[0]) if data else None


def _is_unique_violation(exc: Exception) -> bool:
    """PostgREST surfaces 23505 either via .code attr or message body."""
    code = getattr(exc, "code", None)
    if code == "23505":
        return True
    msg = str(exc).lower()
    return "23505" in msg or "duplicate key" in msg


__all__ = [
    "SequenceStep", "StepChannel", "BranchCondition", "SequenceStepRepository",
]
