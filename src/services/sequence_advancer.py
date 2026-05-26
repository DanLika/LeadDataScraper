"""Sequence advancement — webhook → next-step row creation.

Called by the Instantly webhook handlers (Phase 14.3 + 15.4) when a
terminal event lands on a campaign_messages row. Computes whether to
schedule the next step in the sequence + at what time + with what
branch-condition gate.

**Schedule-on-advance, NOT gate-on-advance**

A naive design would inspect ``step.branch_condition`` at advance time
("`no_reply` → only advance if no reply"). That re-introduces the
exact race we want to avoid: the _replied event might land after we
already advanced + dispatched the next step.

The shipped design (recommended by spec):

  * ``_sent`` event → ALWAYS advance to the next sequential step,
    UNLESS the next step's branch_condition is ``'replied'`` (a
    reply-nurture-only branch — different track).
  * ``_replied`` event → cancel any pending step in the current
    sequence (kills the "no_reply" + "always" continuation), THEN
    advance into the ``'replied'`` branch if the next step is
    marked that way.
  * ``_bounced`` / ``_unsubscribed`` → cancel pending; no advance.

Net effect: the ``no_reply`` branch behaves correctly without
gate-on-advance — the next step is scheduled by _sent, then cancelled
by _replied if the recipient replies before the schedule fires. The
``replied`` branch is inverted: only _replied advances into it.

**Window-aware scheduling**

The computed ``scheduled_at`` walks through the next step's window
config: if ``sent_at + delay`` falls outside the next step's
send_window / send_days, bump to the next valid window start. Uses
``src/utils/send_window.is_within_window`` for the resolver.

Race notes captured in :mod:`docs/sequencing-architecture.md`
§ Known races; same surface caveats apply (dispatching-row inflight
escape, 1-tick recovery lag, etc.).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

EventType = Literal["sent", "replied"]


@dataclass(frozen=True)
class AdvanceResult:
    """Per-call outcome envelope. Webhook handler logs this; no HTTP
    surface (handler returns 200 to provider regardless)."""

    advanced: bool
    next_step_id: Optional[str] = None
    next_message_id: Optional[str] = None
    scheduled_at: Optional[str] = None
    reason: Optional[str] = None


async def advance_to_next_step(
    *,
    current_message: dict[str, Any],
    step_repo: Any,        # SequenceStepRepository
    message_repo: Any,     # CampaignMessageRepository
    event_type: EventType,
    sent_at: Optional[datetime] = None,
) -> AdvanceResult:
    """Compute + create the next step's pending row.

    Returns :class:`AdvanceResult`. ``advanced=True`` means a row was
    INSERTed; False with ``reason`` set when the path short-circuits
    (sequence complete, branch-gating excludes advance,
    UNIQUE-collision from a duplicate event, etc.).

    Required ``current_message`` fields:
      * id (UUID str)
      * lead_unique_key
      * campaign_id
      * sequence_id
      * step_id (current step — we walk to step_index+1)
      * provider_message_id (used as ``in_reply_to_message_id`` on
        the next-step row when the next step is ``thread_with_prior``)
    """
    seq_id = current_message.get("sequence_id")
    cur_step_id = current_message.get("step_id")
    lead_uk = current_message.get("lead_unique_key")
    campaign_id = current_message.get("campaign_id")

    if not (seq_id and cur_step_id and lead_uk and campaign_id):
        return AdvanceResult(
            advanced=False,
            reason="missing_sequence_context",
        )

    # Load current step to find its index, then look up step_index+1.
    cur_step = await step_repo.get_by_id(cur_step_id) if hasattr(
        step_repo, "get_by_id",
    ) else None
    if cur_step is None:
        # Fall back: fetch via fetch_many for compatibility.
        fetched = await step_repo.fetch_many([cur_step_id])
        cur_step = fetched.get(cur_step_id)
    if cur_step is None:
        return AdvanceResult(
            advanced=False,
            reason="current_step_not_found",
        )

    next_index = cur_step.step_index + 1
    next_step = await step_repo.get_by_index(seq_id, next_index)
    if next_step is None:
        return AdvanceResult(
            advanced=False,
            reason="sequence_complete",
        )

    # Branch-condition gating — schedule-on-advance per the design
    # decision documented above. _sent advances UNLESS next step is
    # reply-only. _replied advances ONLY if next step IS reply-only.
    next_branch = getattr(next_step, "branch_condition", "always")
    if event_type == "sent" and next_branch == "replied":
        return AdvanceResult(
            advanced=False,
            reason="next_step_replied_only",
        )
    if event_type == "replied" and next_branch != "replied":
        return AdvanceResult(
            advanced=False,
            reason="next_step_not_replied_branch",
        )

    # Compute scheduled_at = sent_at + delay, then bump to next valid
    # send window if outside.
    base = sent_at or _now_utc()
    raw_scheduled = base + timedelta(
        days=int(getattr(next_step, "delay_days", 0) or 0),
        hours=int(getattr(next_step, "delay_hours", 0) or 0),
    )
    scheduled_utc = _bump_to_window(
        raw_scheduled,
        send_window_start=next_step.send_window_start,
        send_window_end=next_step.send_window_end,
        send_days=next_step.send_days,
    )

    in_reply_to = None
    if getattr(next_step, "thread_with_prior", False):
        in_reply_to = current_message.get("provider_message_id")

    inserted = await message_repo.insert_next_step_row(
        lead_unique_key=lead_uk,
        campaign_id=campaign_id,
        sequence_id=seq_id,
        step_id=next_step.id,
        channel=next_step.channel,
        scheduled_at_iso=scheduled_utc.isoformat(),
        in_reply_to_message_id=in_reply_to,
    )
    if inserted is None:
        # Idempotent replay (UNIQUE collision on partial index) OR
        # repo-level error. Logged at the repo boundary.
        return AdvanceResult(
            advanced=False,
            reason="insert_skipped_or_duplicate",
            next_step_id=next_step.id,
            scheduled_at=scheduled_utc.isoformat(),
        )
    return AdvanceResult(
        advanced=True,
        next_step_id=next_step.id,
        next_message_id=inserted.get("id"),
        scheduled_at=scheduled_utc.isoformat(),
    )


# ----- Internals ------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _bump_to_window(
    when: datetime,
    *,
    send_window_start: str,
    send_window_end: str,
    send_days: str,
) -> datetime:
    """If ``when`` is outside the (window, days) config, return the next
    valid window start (in UTC). Otherwise return ``when`` unchanged.
    """
    from src.utils.send_window import is_within_window

    tz_name = os.environ.get("SEND_WINDOW_DEFAULT_TZ") or "UTC"
    check = is_within_window(
        step_send_window_start=send_window_start,
        step_send_window_end=send_window_end,
        step_send_days=send_days,
        timezone_name=tz_name,
        now_utc=when,
    )
    if check.in_window:
        return when
    return check.next_window_start_utc or when


__all__ = ["AdvanceResult", "EventType", "advance_to_next_step"]
