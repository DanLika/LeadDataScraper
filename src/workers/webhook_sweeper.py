"""Webhook event sweeper — Render Cron entry point.

Catches rows in ``webhook_events`` that landed but never advanced past
``processed_at IS NULL``. The only producer of that shape today is the
``/webhooks/instantly`` handler's transport-error path: if supabase-py
raises an ``httpx``/``httpcore`` exception AFTER PostgREST has already
committed the INSERT, the row exists but the response never reached
the handler. PR #357 (Path C) re-reads the row before deciding 500 vs
recovered 200 — this worker (Path A) is the catchall for the cases
where the re-read itself fails (same db.client; same connection pool
under load) AND for any future failure mode that lands the same shape.

Per-tick algorithm (idempotent under concurrent runs + concurrent
inbound webhooks):

  1. Claim a batch: ``SELECT id, provider, event_id, event_type,
     payload`` where ``processed_at IS NULL`` AND ``received_at <
     now() - grace`` ORDER BY ``received_at`` ASC LIMIT batch_size.
     Grace defaults to 60s so an in-flight BackgroundTask isn't
     preempted.

  2. For each Instantly row: call ``_process_instantly_event``. The
     handlers do all idempotency themselves (state-machine predicates,
     suppression upsert). ``_process_instantly_event`` already stamps
     ``processed_at`` + ``processing_error`` on the webhook_events row
     at the end of its run, so we don't double-write.

  3. Bounded runtime: stop after ``max_runtime_sec`` (Render Cron has
     a hard timeout) OR after ``batch_size`` rows. Next tick picks up
     the rest. Errors during processing are captured per-row so a
     single poison event doesn't block the rest of the batch.

Concurrency vs. inbound handler:
  * Inbound's ``BackgroundTask`` + the sweeper can both fire for the
    same event_id. ``_instantly_handle_*`` are predicate-idempotent;
    ``processed_at`` UPDATE = last writer wins; nothing breaks.

Concurrency vs. another sweeper run:
  * Render Cron starts a fresh container per tick — two concurrent
    runs are rare but possible during slow rollouts. The grace window
    + ordered claim doesn't lock rows, so both could pick the same
    batch. Re-fires are safe (idempotent handlers); duplicate
    ``processed_at`` writes are no-ops.

Phase 14.X Path A — designed in response to N=200 burst test where
~15% of events under 10-parallel load surfaced
``httpcore.RemoteProtocolError`` post-commit, leaving rows orphaned.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = int(os.environ.get("WEBHOOK_SWEEP_BATCH_SIZE", "50"))
DEFAULT_GRACE_SECONDS = int(os.environ.get("WEBHOOK_SWEEP_GRACE_SEC", "60"))
DEFAULT_MAX_RUNTIME_SEC = int(os.environ.get("WEBHOOK_SWEEP_MAX_RUNTIME_SEC", "50"))


@dataclass
class SweepResult:
    """Structured outcome of one ``sweep_once`` call."""

    scanned: int = 0
    processed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


async def sweep_once(
    *,
    batch_size: int | None = None,
    grace_seconds: int | None = None,
    max_runtime_sec: int | None = None,
    db: Any = None,
    process_instantly_event: Any = None,
) -> SweepResult:
    """One sweeper tick.

    Args:
        batch_size: How many rows to claim per tick. Defaults to env
            ``WEBHOOK_SWEEP_BATCH_SIZE`` or 50.
        grace_seconds: Skip rows newer than now - grace. Defaults to
            env ``WEBHOOK_SWEEP_GRACE_SEC`` or 60.
        max_runtime_sec: Wall-clock cap. Defaults to env
            ``WEBHOOK_SWEEP_MAX_RUNTIME_SEC`` or 50.
        db: Override the supabase wrapper for unit tests. Production
            passes None and imports the lazy singleton from
            ``src.utils.supabase_helper``.
        process_instantly_event: Override the event-handler dispatch
            for unit tests. Production passes None and imports
            ``backend.main._process_instantly_event``.

    Returns:
        :class:`SweepResult` with structured per-stage counts.
    """
    batch = batch_size if batch_size is not None else DEFAULT_BATCH_SIZE
    grace = grace_seconds if grace_seconds is not None else DEFAULT_GRACE_SECONDS
    deadline = time.monotonic() + (
        max_runtime_sec if max_runtime_sec is not None else DEFAULT_MAX_RUNTIME_SEC
    )

    if db is None:
        # supabase_helper exports SupabaseHelper *class*; the lazy
        # singleton lives on backend.main (PEP 562 __getattr__).
        # Render Cron runs scripts/webhook_sweeper.py as a one-shot
        # process — no lifespan to prime backend.main.db — so we
        # instantiate fresh per tick. Each cron invocation gets its
        # own client + connection pool; cheap relative to the
        # PostgREST work the tick does anyway.
        from src.utils.supabase_helper import SupabaseHelper
        db = SupabaseHelper()
    if process_instantly_event is None:
        from backend.main import _process_instantly_event
        process_instantly_event = _process_instantly_event

    result = SweepResult()
    started = time.monotonic()

    if not getattr(db, "client", None):
        result.errors.append("db_client_unavailable")
        result.duration_ms = (time.monotonic() - started) * 1000
        return result

    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=grace)).isoformat()

    try:
        rows_resp = await asyncio.to_thread(
            lambda: db.client.table("webhook_events")
            .select("id, provider, event_id, event_type, payload, received_at")
            .is_("processed_at", "null")
            .lt("received_at", cutoff)
            .order("received_at", desc=False)
            .limit(batch)
            .execute()
        )
        rows = getattr(rows_resp, "data", None) or []
    except Exception as exc:
        result.errors.append(f"claim_failed:{type(exc).__name__}")
        logger.exception("webhook_sweeper claim failed")
        result.duration_ms = (time.monotonic() - started) * 1000
        return result

    result.scanned = len(rows)

    for row in rows:
        if time.monotonic() > deadline:
            result.errors.append("runtime_cap")
            break

        provider = row.get("provider")
        if provider != "instantly":
            # Future-proof: only Instantly handler exists today. Skip
            # non-Instantly providers so this worker can ship before
            # the Resend/HeyReach dispatchers add their own handlers.
            result.skipped += 1
            continue

        event_id = row.get("event_id") or ""
        try:
            await process_instantly_event(
                event_id=event_id,
                payload=row.get("payload") or {},
            )
            result.processed += 1
        except Exception as exc:
            result.failed += 1
            result.errors.append(f"{event_id}:{type(exc).__name__}")
            logger.exception(
                "webhook_sweeper handler failed event_id=%s",
                event_id,
                extra={"event_id": event_id, "event_type": row.get("event_type")},
            )
            # Don't break — keep sweeping. _process_instantly_event
            # writes processing_error on the row even when handlers
            # raise, so the next tick won't immediately re-pick it.

    result.duration_ms = (time.monotonic() - started) * 1000
    return result


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_GRACE_SECONDS",
    "DEFAULT_MAX_RUNTIME_SEC",
    "SweepResult",
    "sweep_once",
]
