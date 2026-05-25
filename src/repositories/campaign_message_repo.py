"""CampaignMessageRepository — PostgREST I/O for campaign_messages.

Phase 14.3 — closes the dispatcher round-trip loop. The webhook handler
calls these methods on every Instantly event; the dispatcher calls
``mark_send_failed`` for per-lead API errors. Both surfaces share the
same idempotent UPDATE pattern so out-of-order webhooks don't corrupt
state.

State-machine matrix
--------------------

::

    pending  ─── email_sent ───→ sent
                                  │
                                  ├── email_bounced ──→ bounced
                                  ├── email_unsub ────→ unsubscribed
                                  └── email_replied ──→ replied

Every transition uses a single PostgREST UPDATE with a predicate that
restricts firing to the allowed source states. Re-applying the same
event (Instantly retries on any non-2xx + occasionally on 2xx) becomes
a no-op because the predicate matches zero rows after the first apply.

PostgREST does not support raw SQL — see CLAUDE.md "Connection pool /
pooler-URL contract". All UPDATEs flow through the chain API, which
maps cleanly to the state-machine predicates.

Race conditions documented in the method docstrings:

- ``mark_sent``: first-hit-wins via ``.is_("provider_message_id", "null")``
  predicate. Replay-safe.
- ``mark_bounced``: matches by ``provider_message_id`` from the bounce
  event. If ``email_sent`` hasn't processed yet (out-of-order arrival
  from Instantly), the row's ``provider_message_id`` is still NULL and
  the bounce UPDATE matches zero rows. **Acceptable degraded state** —
  the suppression INSERT still happens via recipient_email, so the
  dispatcher precheck (PR α) catches the address on the next send.
  ``campaign_messages.status`` stays ``pending`` and the operator
  sees a stale state until the late ``email_sent`` catches up.
- ``mark_unsubscribed`` / ``mark_replied``: same out-of-order risk.
  Same mitigation (suppression INSERT via email on unsubscribe).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarkResult:
    """Outcome of one of the ``mark_*`` calls.

    ``matched`` reflects whether the predicate matched a row (some
    PostgREST responses surface the count; supabase-py exposes
    ``len(response.data)`` after a returning UPDATE). When unknown
    (older client / mock), defaults to False — callers should treat
    ``MarkResult(matched=False)`` as "either no-op or unverified" and
    not as a hard failure.
    """

    matched: bool
    error: Optional[str] = None


class CampaignMessageRepository:
    """PostgREST adapter for ``public.campaign_messages``.

    Stateless; construct with the same supabase-py client used by the
    rest of the stack (``SupabaseHelper().client``). All updates are
    PostgREST-chained — no raw SQL, no psycopg.
    """

    TABLE_NAME = "campaign_messages"

    def __init__(self, db: Any) -> None:
        self._db = db

    # ----- Webhook-driven transitions -------------------------------------

    async def mark_sent(
        self,
        lds_message_id: str,
        provider_message_id: str,
        *,
        sent_at_iso: str,
    ) -> MarkResult:
        """Stamp provider_message_id + status='sent' + sent_at.

        Predicate: ``id = lds_message_id AND provider_message_id IS NULL``.
        First-hit-wins: subsequent webhook replays (Instantly retries on
        2xx, sometimes) see provider_message_id already set and match
        zero rows — no-op.

        ``lds_message_id`` is the campaign_messages.id UUID. The
        dispatcher passes it as ``custom_variables.lds_message_id`` per
        Phase 14.3 wiring; Instantly echoes it back in every event
        related to the same message. The webhook handler extracts it
        from ``event.custom_variables`` and forwards here.
        """
        if not self._db or not lds_message_id or not provider_message_id:
            return MarkResult(matched=False, error="missing identifiers")
        try:
            res = await asyncio.to_thread(
                lambda: (
                    self._db.table(self.TABLE_NAME)
                    .update({
                        "provider_message_id": provider_message_id,
                        "status": "sent",
                        "sent_at": sent_at_iso,
                    })
                    .eq("id", lds_message_id)
                    .is_("provider_message_id", "null")
                    .execute()
                )
            )
        except Exception as exc:  # noqa: BLE001 — boundary catch
            logger.exception(
                "mark_sent failed for lds_message_id=%s", lds_message_id,
            )
            return MarkResult(matched=False, error=type(exc).__name__)
        matched = bool(getattr(res, "data", None))
        return MarkResult(matched=matched)

    async def mark_bounced(
        self,
        provider_message_id: str,
        *,
        bounce_reason: str = "",
    ) -> MarkResult:
        """Transition sent|pending → bounced. Stamp bounce_reason.

        Predicate: ``provider_message_id = X AND status IN ('pending', 'sent')``.
        Bounce-on-already-bounced is a no-op (predicate excludes terminal
        states). A bounce arriving before the email_sent webhook is
        documented in the module docstring — predicate matches zero rows
        in that case; suppression INSERT still fires via recipient_email.

        Note: PostgREST `.in_()` syntax is the equivalent of SQL
        ``IN (...)`` — matches if status is any of the listed values.
        """
        if not self._db or not provider_message_id:
            return MarkResult(matched=False, error="missing provider_message_id")
        try:
            res = await asyncio.to_thread(
                lambda: (
                    self._db.table(self.TABLE_NAME)
                    .update({
                        "status": "bounced",
                        "bounce_reason": (bounce_reason or "")[:200] or None,
                    })
                    .eq("provider_message_id", provider_message_id)
                    .in_("status", ["pending", "sent"])
                    .execute()
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "mark_bounced failed for provider_message_id=%s",
                provider_message_id,
            )
            return MarkResult(matched=False, error=type(exc).__name__)
        return MarkResult(matched=bool(getattr(res, "data", None)))

    async def mark_unsubscribed(
        self,
        provider_message_id: str,
    ) -> MarkResult:
        """Transition pending|sent|replied → unsubscribed.

        Replied → unsubscribed IS allowed (a recipient can reply
        positively and later unsubscribe). bounced → unsubscribed is
        NOT allowed (a bounced address can't unsubscribe; the bounce
        itself is the terminal state and any follow-up event is
        spurious).
        """
        if not self._db or not provider_message_id:
            return MarkResult(matched=False, error="missing provider_message_id")
        try:
            res = await asyncio.to_thread(
                lambda: (
                    self._db.table(self.TABLE_NAME)
                    .update({"status": "unsubscribed"})
                    .eq("provider_message_id", provider_message_id)
                    .in_("status", ["pending", "sent", "replied"])
                    .execute()
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "mark_unsubscribed failed for provider_message_id=%s",
                provider_message_id,
            )
            return MarkResult(matched=False, error=type(exc).__name__)
        return MarkResult(matched=bool(getattr(res, "data", None)))

    async def mark_replied(self, provider_message_id: str) -> MarkResult:
        """Transition sent → replied.

        Pending → replied is NOT allowed (the dispatcher would have
        had to skip the API call but a reply still arrived — impossible
        with the current model; the row's status must be 'sent' or
        later). Reply-on-bounced is excluded — a bounced address can't
        legitimately reply.
        """
        if not self._db or not provider_message_id:
            return MarkResult(matched=False, error="missing provider_message_id")
        try:
            res = await asyncio.to_thread(
                lambda: (
                    self._db.table(self.TABLE_NAME)
                    .update({"status": "replied"})
                    .eq("provider_message_id", provider_message_id)
                    .in_("status", ["sent"])
                    .execute()
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "mark_replied failed for provider_message_id=%s",
                provider_message_id,
            )
            return MarkResult(matched=False, error=type(exc).__name__)
        return MarkResult(matched=bool(getattr(res, "data", None)))

    # ----- Dispatcher-driven failure path ---------------------------------

    async def mark_send_failed(
        self,
        lds_message_id: str,
        *,
        error: str,
    ) -> MarkResult:
        """Transition pending → bounced after Instantly /leads/add rejects.

        Path: dispatcher pushed the lead, API returned 400/422/etc., the
        row never made it through. Subsequent webhooks won't fire (the
        send never happened). We mark bounced with a synthetic
        ``bounce_reason`` so the campaign-messages export tells the
        operator what happened.

        The state-machine transition is intentionally re-using 'bounced'
        — Instantly's failure modes (auth, rate, validation) are all
        "this email never went out" and the operator action is the same
        as a hard bounce (manual review + retry / drop). A separate
        'send_failed' status would split that branch without adding
        information.
        """
        if not self._db or not lds_message_id:
            return MarkResult(matched=False, error="missing lds_message_id")
        try:
            res = await asyncio.to_thread(
                lambda: (
                    self._db.table(self.TABLE_NAME)
                    .update({
                        "status": "bounced",
                        "bounce_reason": f"send_failed: {error}"[:200],
                    })
                    .eq("id", lds_message_id)
                    .eq("status", "pending")
                    .execute()
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "mark_send_failed failed for lds_message_id=%s", lds_message_id,
            )
            return MarkResult(matched=False, error=type(exc).__name__)
        return MarkResult(matched=bool(getattr(res, "data", None)))

    # ----- Phase 15.1 dispatch-queue surface ------------------------------

    async def fetch_due_for_dispatch(
        self,
        *,
        limit: int = 100,
        now_iso: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Read the next batch of due-to-send messages.

        Predicate: ``status = 'pending' AND scheduled_at <= now()``
        ORDER BY scheduled_at ASC LIMIT N. Backed by partial index
        ``idx_campaign_messages_dispatch_queue`` (status='pending' AND
        scheduled_at IS NOT NULL).

        Returns raw row dicts (not a typed dataclass) — the
        dispatch_tick worker (Phase 15.2) joins lead + step + variant
        data per row and constructs the send payload from a wider
        projection than this repo's natural surface should expose.

        ``now_iso`` defaults to current UTC; tests override for
        deterministic scheduling.

        Race-safety note: this is a pure read. The atomic
        ``pending → dispatching`` lock-transition lives in
        ``claim_for_dispatch`` (Phase 15.2 PR — adds the 'dispatching'
        status to the CHECK allowlist alongside the claim method).
        """
        if not self._db or limit <= 0:
            return []
        when = now_iso or _now_iso()
        try:
            rows = await asyncio.to_thread(
                lambda: (
                    self._db.table(self.TABLE_NAME)
                    .select("*")
                    .eq("status", "pending")
                    .lte("scheduled_at", when)
                    .order("scheduled_at", desc=False)
                    .limit(limit)
                    .execute()
                )
            )
        except Exception:
            logger.exception(
                "fetch_due_for_dispatch failed (limit=%d, when=%s)",
                limit, when,
            )
            return []
        return list(getattr(rows, "data", None) or [])

    async def schedule_step(
        self,
        message_id: str,
        *,
        step_id: str,
        variant_id: str,
        scheduled_at_iso: str,
    ) -> MarkResult:
        """Stamp the step + variant FKs and the ``scheduled_at`` ts on a
        pending row. Used by the sequence advancement path (Phase 15.4)
        when creating the next-step row OR when re-scheduling a
        rejected send window.

        Predicate: ``id = message_id AND status = 'pending'``. A row
        already in ``sent`` / ``bounced`` / etc. is excluded — the
        sequence advancer only schedules into legitimately-pending
        slots, and a late re-schedule on a terminal row would be a
        bug we want to surface as a no-op rather than silently
        rewrite history.
        """
        if not self._db or not message_id or not step_id or not variant_id:
            return MarkResult(matched=False, error="missing identifiers")
        try:
            res = await asyncio.to_thread(
                lambda: (
                    self._db.table(self.TABLE_NAME)
                    .update({
                        "step_id": step_id,
                        "variant_id": variant_id,
                        "scheduled_at": scheduled_at_iso,
                    })
                    .eq("id", message_id)
                    .eq("status", "pending")
                    .execute()
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "schedule_step failed for message_id=%s", message_id,
            )
            return MarkResult(matched=False, error=type(exc).__name__)
        return MarkResult(matched=bool(getattr(res, "data", None)))

    # ----- Phase 15.2 dispatch claim surface -------------------------------
    #
    # Concurrent-worker safety pattern (canonical for this repo and any
    # follow-up worker repo):
    #
    #   PostgREST has NO ``SELECT FOR UPDATE SKIP LOCKED``. The equivalent
    #   is a two-phase status-transition claim that relies on PG's
    #   row-level serialization in READ COMMITTED:
    #
    #   Phase 1: SELECT due ids (via :meth:`fetch_due_for_dispatch`)
    #   Phase 2: UPDATE SET status='dispatching', dispatched_at=now()
    #            WHERE id IN (ids) AND status='pending'
    #            RETURNING *
    #
    #   Two ticks SELECTing the same ids both attempt Phase 2; the first
    #   UPDATE flips each row's status; the second's ``status='pending'``
    #   predicate matches zero rows for already-claimed ones. Wasted
    #   SELECT work but no double-dispatch.
    #
    #   The stale-claim sweeper (separate method) handles the crash
    #   scenario where a worker dies mid-tick leaving rows stuck in
    #   ``'dispatching'``. Sweeper runs at the top of every tick.

    async def claim_due_batch(
        self,
        *,
        limit: int = 100,
        now_iso: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Atomically claim up to ``limit`` due-to-send messages.

        Two-phase status-transition claim (see comment above): Phase 1
        reads due ids via :meth:`fetch_due_for_dispatch`. Phase 2 flips
        them ``pending → dispatching`` with a ``status='pending'``
        predicate that any concurrent worker's UPDATE matches against
        zero rows after losing the row-level race.

        Returns the rows that THIS tick successfully claimed.

        Caller MUST release the lock by either:
          * the dispatcher success path → webhook → ``mark_sent`` /
            ``mark_bounced`` (transitions to a terminal status), OR
          * waiting for :meth:`sweep_stale_claims` to revert after
            ``DISPATCH_CLAIM_TIMEOUT_MIN``.
        """
        if not self._db or limit <= 0:
            return []
        when = now_iso or _now_iso()
        due_rows = await self.fetch_due_for_dispatch(limit=limit, now_iso=when)
        if not due_rows:
            return []
        due_ids = [r.get("id") for r in due_rows if r.get("id")]
        if not due_ids:
            return []
        try:
            res = await asyncio.to_thread(
                lambda: (
                    self._db.table(self.TABLE_NAME)
                    .update({
                        "status": "dispatching",
                        "dispatched_at": when,
                    })
                    .in_("id", due_ids)
                    .eq("status", "pending")
                    .execute()
                )
            )
        except Exception:
            logger.exception(
                "claim_due_batch UPDATE failed (limit=%d, when=%s)",
                limit, when,
            )
            return []
        return list(getattr(res, "data", None) or [])

    async def sweep_stale_claims(
        self,
        *,
        timeout_minutes: int = 15,
        now_iso: Optional[str] = None,
    ) -> int:
        """Reset rows stuck in ``'dispatching'`` past ``timeout_minutes``
        back to ``'pending'`` so the next tick re-claims them.

        Resilience for crashed workers: if a tick wins the claim but
        then SIGKILL / OOM / Render-timeout's mid-dispatch, the row
        stays in ``'dispatching'`` forever without intervention. The
        sweeper runs at the top of every tick (before claim) so the
        recovery window = at most one tick interval + timeout.

        Default 15 min is generous vs the Render Cron 60 s hard
        timeout — a 15× safety margin. Pin via
        ``DISPATCH_CLAIM_TIMEOUT_MIN`` env if tightening for higher
        throughput.

        Returns the number of rows reset.
        """
        if not self._db or timeout_minutes <= 0:
            return 0
        from datetime import datetime, timedelta, timezone
        now = (
            datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
            if now_iso else datetime.now(timezone.utc)
        )
        cutoff = (now - timedelta(minutes=timeout_minutes)).isoformat()
        try:
            res = await asyncio.to_thread(
                lambda: (
                    self._db.table(self.TABLE_NAME)
                    .update({
                        "status": "pending",
                        "dispatched_at": None,
                    })
                    .eq("status", "dispatching")
                    .lt("dispatched_at", cutoff)
                    .execute()
                )
            )
        except Exception:
            logger.exception(
                "sweep_stale_claims failed (timeout_minutes=%d)",
                timeout_minutes,
            )
            return 0
        return len(getattr(res, "data", None) or [])


def _now_iso() -> str:
    """Centralized UTC ISO timestamp — split from inline ``datetime.now()``
    calls so tests can patch a single import site if a future scenario
    needs frozen-clock semantics."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "MarkResult",
    "CampaignMessageRepository",
]
