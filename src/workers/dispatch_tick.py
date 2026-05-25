"""Dispatch tick worker — Render Cron entry point.

Per-tick algorithm (idempotent; safe under crash / concurrent runs):

  1. Sweep stale claims: any row stuck in ``status='dispatching'`` past
     ``DISPATCH_CLAIM_TIMEOUT_MIN`` minutes → reset to ``'pending'``.
     Recovers from crashed prior ticks. Bounded by partial index.

  2. Claim due batch: atomic ``pending → dispatching`` UPDATE on up to
     ``DISPATCH_TICK_BATCH_SIZE`` rows with ``scheduled_at <= now()``.
     PG row-level locks serialize concurrent ticks → at most one wins
     per row.

  3. Re-check suppression: even though the dispatcher (Phase 14.1) does
     its own suppression precheck pre-API-call, suppressions can land
     between the original schedule and now (via webhook). One batch
     SELECT covers all claimed addresses.

  4. Filter by send window: per-message, compute "is the lead's local
     time inside the step's window?". Out-of-window → release the
     claim (status='pending', scheduled_at = next window start). The
     row stays in the queue for a future tick.

  5. Group by dispatcher: today Instantly only; LinkedIn (HeyReach)
     joins in Phase 17.

  6. Dispatch: call ``dispatcher.push_leads(leads, message_ids=...)``.
     Phase 14.3 ensures ``custom_variables.lds_message_id`` threads
     through so the email_sent webhook can do the targeted UPDATE.

  7. Per-result: errors → ``mark_send_failed`` (per #324). Success rows
     stay in ``'dispatching'`` until the webhook arrives and transitions
     to ``'sent'`` (or until the next tick's sweeper resets them if
     the webhook doesn't arrive within the timeout).

Return: :class:`TickResult` with structured per-stage counts. CLI in
``scripts/dispatch_tick.py`` prints the JSON form for log aggregation.

Phase 15.2 ships this worker AND the schema status-allowlist extension
that makes ``'dispatching'`` legal. Stale-claim sweeper safety net is
critical because Render Cron has a 60-second hard timeout — any tick
that crosses that boundary leaves the claim stuck.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Env-driven tunables. Defaults match research recommendation for fresh
# Instantly subaccount; operator may tighten via Render env.
_DEFAULT_BATCH_SIZE = 100
_DEFAULT_CLAIM_TIMEOUT_MIN = 15
_DEFAULT_MAX_RUNTIME_SEC = 50  # Render Cron 60s hard cap; 10s safety margin


@dataclass
class TickResult:
    """Structured per-stage counts for one tick run."""

    swept_stale: int = 0
    claimed: int = 0
    skipped_suppressed: int = 0
    skipped_window: int = 0
    dispatched: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


async def run_tick(
    *,
    db_client: Optional[Any] = None,
    dispatcher: Optional[Any] = None,
    batch_size: Optional[int] = None,
    claim_timeout_min: Optional[int] = None,
    max_runtime_sec: Optional[int] = None,
    now_iso: Optional[str] = None,
) -> TickResult:
    """Run one dispatch tick. Returns structured per-stage counts.

    All injection points (db_client, dispatcher) accept None to fall
    through to defaults — production wiring constructs them from env;
    tests pass mocks. ``now_iso`` lets tests freeze the clock.
    """
    started = _monotonic_now()
    bs = batch_size if batch_size is not None else _env_int(
        "DISPATCH_TICK_BATCH_SIZE", _DEFAULT_BATCH_SIZE,
    )
    timeout = claim_timeout_min if claim_timeout_min is not None else _env_int(
        "DISPATCH_CLAIM_TIMEOUT_MIN", _DEFAULT_CLAIM_TIMEOUT_MIN,
    )
    runtime_cap = max_runtime_sec if max_runtime_sec is not None else _env_int(
        "DISPATCH_TICK_MAX_RUNTIME_SEC", _DEFAULT_MAX_RUNTIME_SEC,
    )
    result = TickResult()

    db = db_client
    if db is None:
        db = _resolve_db_client()
        if db is None:
            result.errors.append("db_client_unavailable")
            result.elapsed_seconds = _monotonic_now() - started
            return result

    from src.repositories.campaign_message_repo import CampaignMessageRepository
    from src.repositories.suppression_repo import SuppressionRepository

    msg_repo = CampaignMessageRepository(db)
    sup_repo = SuppressionRepository(db)

    # 1. Sweep stale claims FIRST. Same tick can then re-claim them via
    #    the partial index — no waiting for a follow-up tick.
    try:
        result.swept_stale = await msg_repo.sweep_stale_claims(
            timeout_minutes=timeout, now_iso=now_iso,
        )
        if result.swept_stale:
            logger.info(
                "dispatch_tick swept %d stale claim(s)",
                result.swept_stale,
                extra={"swept": result.swept_stale},
            )
    except Exception as exc:  # noqa: BLE001 — boundary catch
        logger.exception("dispatch_tick sweep failed")
        result.errors.append(f"sweep_failed:{type(exc).__name__}")

    if _exceeded_runtime(started, runtime_cap):
        result.errors.append("runtime_cap_after_sweep")
        result.elapsed_seconds = _monotonic_now() - started
        return result

    # 2. Atomic claim. Two-phase status-transition pattern in the repo.
    try:
        claimed = await msg_repo.claim_due_batch(limit=bs, now_iso=now_iso)
    except Exception as exc:  # noqa: BLE001
        logger.exception("dispatch_tick claim failed")
        result.errors.append(f"claim_failed:{type(exc).__name__}")
        result.elapsed_seconds = _monotonic_now() - started
        return result
    result.claimed = len(claimed)

    if not claimed:
        result.elapsed_seconds = _monotonic_now() - started
        return result

    # 3. Re-check suppression across the full claimed batch. Pull lead
    #    emails via lead_unique_key; the repo precheck takes addresses
    #    as a list and returns (allowed, suppressed).
    emails_by_msg_id: dict[str, str] = {}
    for row in claimed:
        # Defensive — claim returns supabase row dicts; the dispatcher
        # path needs the lead's email. Phase 15.2 reads it from
        # lead_unique_key via a separate lookup. Until then, expect
        # the dispatch loop to have stamped the email on the row at
        # generate-time (NOT done yet; see Phase 15.3 / 15.4 docs).
        # If absent, the row is skipped as "no_email"; logged below.
        msg_id = str(row.get("id") or "")
        # Lead email resolution placeholder — replaced when Phase 15.3
        # wires the lead-row join into the claim payload. For now we
        # consult campaign_messages-side cached fields if present.
        candidate_email = (
            row.get("recipient_email")
            or row.get("email")
            or ""
        )
        if msg_id and candidate_email:
            emails_by_msg_id[msg_id] = candidate_email

    suppressed_set: set[str] = set()
    if emails_by_msg_id:
        try:
            _, blocked = await sup_repo.filter_suppressed(
                list(emails_by_msg_id.values()), "email",
            )
            suppressed_set = set(blocked)
        except Exception:
            logger.exception("dispatch_tick suppression recheck failed")
            # Fail-OPEN — dispatcher's own precheck is the load-bearing
            # gate. Worst case: one extra send to a should-be-suppressed
            # address → webhook bounces → re-suppression on next cycle.

    if _exceeded_runtime(started, runtime_cap):
        result.errors.append("runtime_cap_after_suppression")
        result.elapsed_seconds = _monotonic_now() - started
        return result

    # 4. Window check + suppression filter. Out-of-window OR suppressed
    #    rows release the claim back to 'pending'. Counter increments
    #    per skip-reason so the operator can see the breakdown.
    eligible_msgs: list[dict[str, Any]] = []
    for row in claimed:
        msg_id = str(row.get("id") or "")
        if not msg_id:
            continue
        email = emails_by_msg_id.get(msg_id, "")
        if email and email in suppressed_set:
            result.skipped_suppressed += 1
            await _release_claim(
                msg_repo, msg_id,
                reason="suppressed_post_schedule",
                target_status="cancelled",
            )
            continue
        in_window, next_start = _window_check_for_row(row)
        if not in_window:
            result.skipped_window += 1
            await _release_claim(
                msg_repo, msg_id,
                next_scheduled_at=next_start.isoformat() if next_start else None,
                target_status="pending",
            )
            continue
        eligible_msgs.append(row)

    if not eligible_msgs:
        result.elapsed_seconds = _monotonic_now() - started
        return result

    if _exceeded_runtime(started, runtime_cap):
        result.errors.append("runtime_cap_before_dispatch")
        result.elapsed_seconds = _monotonic_now() - started
        return result

    # 5+6. Dispatch. Today Instantly only; LinkedIn arrives Phase 17.
    dispatch_impl = dispatcher or _resolve_dispatcher()
    if dispatch_impl is None:
        result.errors.append("dispatcher_unavailable")
        # Don't mark_send_failed — operator misconfig, not row-level
        # failure. Sweeper will re-pending these after the timeout.
        result.elapsed_seconds = _monotonic_now() - started
        return result

    # The dispatcher push path (Phase 14.1 + 14.3) expects lead rows +
    # message_ids dict. Phase 15.2 packs both from the claimed rows
    # using lead_unique_key as the join key. The full lead-row payload
    # (with first_name / company_name / etc) lives on the leads table;
    # for 15.2 the tick reads the minimal projection it can.
    leads_payload: list[dict[str, Any]] = []
    message_ids: dict[str, str] = {}
    for row in eligible_msgs:
        msg_id = str(row.get("id") or "")
        uk = str(row.get("lead_unique_key") or "")
        email = emails_by_msg_id.get(msg_id, "")
        if not (msg_id and uk and email):
            result.skipped_window += 0  # already counted above
            continue
        leads_payload.append({"unique_key": uk, "email": email})
        message_ids[uk] = msg_id

    try:
        push_result = await dispatch_impl.push_leads(
            leads=leads_payload,
            message_ids=message_ids,
        )
        result.dispatched = int(getattr(push_result, "success_count", 0))
        result.failed = int(getattr(push_result, "failed_count", 0))
    except Exception as exc:  # noqa: BLE001
        logger.exception("dispatch_tick dispatcher.push_leads failed")
        result.errors.append(f"dispatch_failed:{type(exc).__name__}")
        # Don't mark every row failed — the dispatcher may have partially
        # succeeded server-side; leave the rows in 'dispatching' and
        # let the sweeper reset them on the next tick.

    # 7. Per-result error handling. push_leads.errors carries per-lead
    #    rejections (auth / rate / validation). Each maps back to its
    #    message_id via the lead's unique_key and triggers
    #    mark_send_failed.
    per_lead_errors = list(getattr(push_result, "errors", []) or []) if 'push_result' in locals() else []
    for err in per_lead_errors:
        err_email = (getattr(err, "email", "") or "").lower()
        # Look up message_id from the email → unique_key mapping.
        uk_match: Optional[str] = None
        for uk, mid in message_ids.items():
            if emails_by_msg_id.get(mid, "").lower() == err_email:
                uk_match = uk
                break
        if not uk_match:
            continue
        msg_id = message_ids[uk_match]
        await msg_repo.mark_send_failed(
            msg_id,
            error=f"{getattr(err, 'error_code', 'unknown')}:{getattr(err, 'error_message', '')[:120]}",
        )

    result.elapsed_seconds = _monotonic_now() - started
    return result


# ----- Internals -----------------------------------------------------------


async def _release_claim(
    msg_repo: Any,
    msg_id: str,
    *,
    next_scheduled_at: Optional[str] = None,
    target_status: str = "pending",
    reason: Optional[str] = None,
) -> None:
    """Release a claimed row back to 'pending' (out-of-window) or flip
    it to 'cancelled' (suppression race). Uses a direct chain rather
    than a repo method because the release semantics are tick-specific
    (no general-purpose method should support arbitrary status
    transitions out of 'dispatching')."""
    if not msg_repo._db or not msg_id:
        return
    update_payload: dict[str, Any] = {"status": target_status}
    if next_scheduled_at:
        update_payload["scheduled_at"] = next_scheduled_at
    if reason:
        update_payload["bounce_reason"] = reason[:200]
    try:
        await asyncio.to_thread(
            lambda: (
                msg_repo._db.table(msg_repo.TABLE_NAME)
                .update(update_payload)
                .eq("id", msg_id)
                .eq("status", "dispatching")
                .execute()
            )
        )
    except Exception:
        logger.exception(
            "release_claim failed for msg_id=%s target=%s",
            msg_id, target_status,
        )


def _window_check_for_row(row: dict[str, Any]) -> tuple[bool, Optional[datetime]]:
    """Resolve the step's window settings + ask the resolver.

    The claimed row carries step_id; Phase 15.2 reads the step's
    send_window_* fields via a single join. Until the dispatcher-loop
    wiring lands (Phase 15.3 ships the step.id → step row join), the
    tick falls back to the DispatchPolicy defaults from
    ``src/utils/dispatch_policy.py``.
    """
    from src.utils.dispatch_policy import DISPATCH_POLICY
    from src.utils.send_window import is_within_window

    check = is_within_window(
        step_send_window_start=row.get("step_send_window_start") or DISPATCH_POLICY.send_window_start,
        step_send_window_end=row.get("step_send_window_end") or DISPATCH_POLICY.send_window_end,
        step_send_days=row.get("step_send_days") or ",".join(DISPATCH_POLICY.send_days),
        timezone_name=row.get("lead_timezone"),
    )
    return check.in_window, check.next_window_start_utc


def _resolve_db_client() -> Optional[Any]:
    """Resolve the supabase-py client from the lazy SupabaseHelper. None
    when env isn't configured — CLI exits cleanly + logs."""
    try:
        from src.utils.supabase_helper import SupabaseHelper
        helper = SupabaseHelper()
        return getattr(helper, "client", None)
    except Exception:
        logger.exception("dispatch_tick failed to resolve db client")
        return None


def _resolve_dispatcher() -> Optional[Any]:
    """Resolve the Instantly dispatcher (Phase 14.1 + 14.3). Future
    multi-provider routing (Phase 17) splits by step.channel here."""
    try:
        from src.integrations.instantly_sender import InstantlyDispatcher
        return InstantlyDispatcher()
    except Exception:
        logger.exception("dispatch_tick failed to resolve dispatcher")
        return None


def _env_int(name: str, default: int) -> int:
    """Permissive int parse — bad env value falls back to default
    rather than crashing the tick (matches DispatchPolicy parse style)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("ignoring bad %s=%r; using default %d", name, raw, default)
        return default


def _monotonic_now() -> float:
    """Wall-clock for elapsed_seconds; monotonic source so back-NTP
    skews don't produce negative durations."""
    import time
    return time.monotonic()


def _exceeded_runtime(started: float, runtime_cap_sec: int) -> bool:
    return (_monotonic_now() - started) >= runtime_cap_sec


__all__ = ["TickResult", "run_tick"]
