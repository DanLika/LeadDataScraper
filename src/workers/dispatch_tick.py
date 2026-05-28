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
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Env-driven tunables. Defaults match research recommendation for fresh
# Instantly subaccount; operator may tighten via Render env.
_DEFAULT_BATCH_SIZE = 100
_DEFAULT_CLAIM_TIMEOUT_MIN = 15
_DEFAULT_MAX_RUNTIME_SEC = 50  # Render Cron 60s hard cap; 10s safety margin


@dataclass
class TickResult:
    """Structured per-stage counts for one tick run.

    ``swallowed`` (Issue #367) counts per-lead errors from
    ``push_leads`` that could NOT be reconciled to a claimed
    ``message_id`` — distinct from ``failed`` (provider-reported
    failure count) and from ``result.errors`` (tick-level error tags).
    Operator sees stranded ``dispatching`` rows in the summary log
    immediately rather than waiting for the stale-claim sweeper.
    """

    swept_stale: int = 0
    claimed: int = 0
    skipped_suppressed: int = 0
    skipped_window: int = 0
    dispatched: int = 0
    failed: int = 0
    swallowed: int = 0
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

    Issue #367 invariant: the ``dispatch_tick summary`` log line is
    emitted on EVERY return path (try/finally) so cron output is
    auditable without operator-side wrapper scripts.
    """
    started = _monotonic_now()
    result = TickResult()
    try:
        await _run_tick_inner(
            result,
            started=started,
            db_client=db_client,
            dispatcher=dispatcher,
            batch_size=batch_size,
            claim_timeout_min=claim_timeout_min,
            max_runtime_sec=max_runtime_sec,
            now_iso=now_iso,
        )
    finally:
        result.elapsed_seconds = _monotonic_now() - started
        logger.info("dispatch_tick summary", extra=result.as_dict())
    return result


async def _run_tick_inner(
    result: TickResult,
    *,
    started: float,
    db_client: Optional[Any],
    dispatcher: Optional[Any],
    batch_size: Optional[int],
    claim_timeout_min: Optional[int],
    max_runtime_sec: Optional[int],
    now_iso: Optional[str],
) -> None:
    """Inner body of :func:`run_tick`. Mutates ``result`` in place;
    never returns a value. Outer wrapper finalizes
    ``elapsed_seconds`` + summary log inside its ``finally`` so every
    early exit remains observable (Issue #367)."""
    bs = (
        batch_size
        if batch_size is not None
        else _env_int(
            "DISPATCH_TICK_BATCH_SIZE",
            _DEFAULT_BATCH_SIZE,
        )
    )
    timeout = (
        claim_timeout_min
        if claim_timeout_min is not None
        else _env_int(
            "DISPATCH_CLAIM_TIMEOUT_MIN",
            _DEFAULT_CLAIM_TIMEOUT_MIN,
        )
    )
    runtime_cap = (
        max_runtime_sec
        if max_runtime_sec is not None
        else _env_int(
            "DISPATCH_TICK_MAX_RUNTIME_SEC",
            _DEFAULT_MAX_RUNTIME_SEC,
        )
    )

    db = db_client
    if db is None:
        db = _resolve_db_client()
        if db is None:
            result.errors.append("db_client_unavailable")
            return

    from src.repositories.campaign_message_repo import CampaignMessageRepository
    from src.repositories.suppression_repo import SuppressionRepository

    msg_repo = CampaignMessageRepository(db)
    sup_repo = SuppressionRepository(db)

    # 1. Sweep stale claims FIRST. Same tick can then re-claim them via
    #    the partial index — no waiting for a follow-up tick.
    try:
        result.swept_stale = await msg_repo.sweep_stale_claims(
            timeout_minutes=timeout,
            now_iso=now_iso,
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
        return

    # 2. Atomic claim. Two-phase status-transition pattern in the repo.
    try:
        claimed = await msg_repo.claim_due_batch(limit=bs, now_iso=now_iso)
    except Exception as exc:  # noqa: BLE001
        logger.exception("dispatch_tick claim failed")
        result.errors.append(f"claim_failed:{type(exc).__name__}")
        return
    result.claimed = len(claimed)

    if not claimed:
        return

    # 3. Phase 15.3 batch-fetch — 4 PostgREST calls regardless of claim
    #    size, instead of N+1 per claimed row. Order:
    #       fetch_many(leads)            ← lead_unique_keys
    #       fetch_many(steps)            ← step_ids
    #       fetch_many_for_steps(variants) ← step_ids
    #       fetch_many(prior_messages)   ← in_reply_to_message_ids
    from src.repositories.lead_repo import LeadRepository
    from src.repositories.sequence_step_repo import SequenceStepRepository
    from src.repositories.sequence_variant_repo import SequenceVariantRepository
    from src.services.thread_builder import (
        PriorMessageNotReadyError,
        ThreadBuildError,
        build_send_payload,
    )
    from src.services.template_renderer import TemplateError
    from src.services.variant_selector import select_variant
    from src.utils.unsubscribe_tokens import build_unsubscribe_url

    lead_repo = LeadRepository(db)
    step_repo = SequenceStepRepository(db)
    variant_repo = SequenceVariantRepository(db)

    lead_uks = {str(r["lead_unique_key"]) for r in claimed if r.get("lead_unique_key")}
    step_ids = {str(r["step_id"]) for r in claimed if r.get("step_id")}
    prior_msg_ids = {
        str(r["in_reply_to_message_id"])
        for r in claimed
        if r.get("in_reply_to_message_id")
    }

    try:
        leads_by_uk = await lead_repo.fetch_many(lead_uks)
        steps_by_id = await step_repo.fetch_many(step_ids)
        variants_by_step = await variant_repo.fetch_many_for_steps(step_ids)
        prior_msgs_by_id = await msg_repo.fetch_many(prior_msg_ids)
    except Exception:  # noqa: BLE001 — single boundary catch
        logger.exception("dispatch_tick batch fetch failed")
        result.errors.append("batch_fetch_failed")
        return

    # 4. Suppression precheck — emails now sourced from leads_by_uk
    #    (the canonical projection) rather than the campaign_messages
    #    placeholder field from Phase 15.2.
    emails_by_msg_id: dict[str, str] = {}
    for row in claimed:
        msg_id = str(row.get("id") or "")
        uk = str(row.get("lead_unique_key") or "")
        lead = leads_by_uk.get(uk, {})
        email = str(lead.get("email") or "").strip().lower()
        if msg_id and email:
            emails_by_msg_id[msg_id] = email

    suppressed_set: set[str] = set()
    if emails_by_msg_id:
        try:
            _, blocked = await sup_repo.filter_suppressed(
                list(set(emails_by_msg_id.values())),
                "email",
            )
            suppressed_set = set(blocked)
        except Exception:
            logger.exception("dispatch_tick suppression recheck failed")
            # Fail-OPEN — dispatcher's own precheck is the load-bearing
            # gate. Worst case: one extra send to a should-be-suppressed
            # address → webhook bounces → re-suppression on next cycle.

    if _exceeded_runtime(started, runtime_cap):
        result.errors.append("runtime_cap_after_suppression")
        return

    # 5. Build the per-message dispatch payloads. Filter out:
    #     * no_email          → release as 'failed' (lead deleted /
    #                            email blanked since schedule)
    #     * suppression       → release as 'cancelled'
    #     * missing step      → release as 'failed' (legacy / orphan row)
    #     * out-of-window     → release back to 'pending' with bumped
    #                            scheduled_at
    #     * no variants       → release as 'failed' (config bug)
    #     * PriorMessageNotReadyError → release as 'pending' with
    #                            +1h bump (race vs prior step's webhook)
    #     * render error      → release as 'failed' with error reason
    dispatch_impl = dispatcher or _resolve_dispatcher()
    if dispatch_impl is None:
        result.errors.append("dispatcher_unavailable")
        return

    operator_name = (os.environ.get("OPERATOR_NAME") or "").strip()
    operator_signature = (os.environ.get("OPERATOR_SIGNATURE") or "").strip()
    unsubscribe_base = (os.environ.get("UNSUBSCRIBE_BASE_URL") or "").rstrip("/")

    leads_payload: list[dict[str, Any]] = []
    message_ids: dict[str, str] = {}
    list_unsubscribe_urls: dict[str, str] = {}

    for row in claimed:
        msg_id = str(row.get("id") or "")
        uk = str(row.get("lead_unique_key") or "")
        if not msg_id or not uk:
            continue

        email = emails_by_msg_id.get(msg_id, "")
        lead = leads_by_uk.get(uk)
        if not email or not lead:
            await _release_claim(
                msg_repo,
                msg_id,
                target_status="failed",
                reason="no_email_or_lead_row",
            )
            result.failed += 1
            continue

        if email in suppressed_set:
            result.skipped_suppressed += 1
            await _release_claim(
                msg_repo,
                msg_id,
                reason="suppressed_post_schedule",
                target_status="cancelled",
            )
            continue

        step_id = str(row.get("step_id") or "")
        step = steps_by_id.get(step_id) if step_id else None
        if not step:
            await _release_claim(
                msg_repo,
                msg_id,
                target_status="failed",
                reason="missing_step",
            )
            result.failed += 1
            continue

        in_window, next_start = _window_check_for_step(
            step,
            lead_timezone=lead.get("timezone"),
        )
        if not in_window:
            result.skipped_window += 1
            await _release_claim(
                msg_repo,
                msg_id,
                next_scheduled_at=(next_start.isoformat() if next_start else None),
                target_status="pending",
            )
            continue

        step_variants = variants_by_step.get(step_id) or []
        chosen_variant = select_variant(
            step_variants,
            deterministic_seed=msg_id,
        )
        if not chosen_variant:
            await _release_claim(
                msg_repo,
                msg_id,
                target_status="failed",
                reason="no_variants",
            )
            result.failed += 1
            continue

        prior_id = str(row.get("in_reply_to_message_id") or "")
        prior_message = prior_msgs_by_id.get(prior_id) if prior_id else None

        tracking_id = str(row.get("tracking_id") or "")
        unsubscribe_url = (
            build_unsubscribe_url(unsubscribe_base, tracking_id)
            if unsubscribe_base and tracking_id
            else ""
        )

        try:
            payload = build_send_payload(
                lds_message_id=msg_id,
                lead=lead,
                step=step,
                variant=chosen_variant,
                prior_message=prior_message,
                operator_name=operator_name,
                operator_signature=operator_signature,
                unsubscribe_url=unsubscribe_url,
            )
        except PriorMessageNotReadyError:
            # Step N+1 scheduled before step N's webhook landed —
            # bump +1h, leave pending. Sweeper handles longer outages.
            from datetime import datetime, timedelta, timezone

            bumped = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            await _release_claim(
                msg_repo,
                msg_id,
                next_scheduled_at=bumped,
                target_status="pending",
            )
            result.skipped_window += 1
            continue
        except (TemplateError, ThreadBuildError) as exc:
            logger.exception(
                "dispatch_tick payload build failed for msg=%s",
                msg_id,
            )
            await _release_claim(
                msg_repo,
                msg_id,
                target_status="failed",
                reason=f"render_error:{type(exc).__name__}",
            )
            result.failed += 1
            continue

        leads_payload.append(payload.as_lead_dict())
        message_ids[uk] = msg_id
        if payload.list_unsubscribe_url:
            list_unsubscribe_urls[uk] = payload.list_unsubscribe_url

    if not leads_payload:
        return

    if _exceeded_runtime(started, runtime_cap):
        result.errors.append("runtime_cap_before_dispatch")
        return

    # 6. Dispatch. push_leads may raise (network / transport) OR return
    #    with per-lead errors in push_result.errors. Both paths produce
    #    distinct telemetry — see Issue #367.
    push_result: Optional[Any] = None
    try:
        push_result = await dispatch_impl.push_leads(
            leads=leads_payload,
            message_ids=message_ids,
            list_unsubscribe_urls=list_unsubscribe_urls,
        )
        result.dispatched = int(getattr(push_result, "success_count", 0))
        # Additive — result.failed already carries pre-dispatch rejects
        # (no_email / missing_step / no_variants / render errors).
        result.failed += int(getattr(push_result, "failed_count", 0))
    except Exception as exc:  # noqa: BLE001
        logger.exception("dispatch_tick dispatcher.push_leads failed")
        result.errors.append(f"dispatch_failed:{type(exc).__name__}")
        # Don't mark every row failed — the dispatcher may have partially
        # succeeded server-side; sweeper resets the rows on the next tick.
        # Per-lead fan-out skipped: push_result has no per-lead detail.
        return

    # 7. Per-lead error fan-out (Issue #367). Buckets, tracked
    #    SEPARATELY in the summary log so operators distinguish:
    #      * matched → mark_send_failed (dispatching → bounced) + INFO
    #      * single-batch fallback (1 claim + 1 error, no email match)
    #          → mark_send_failed + WARNING
    #      * unmatched in multi-batch → ``result.swallowed += 1`` +
    #          WARNING + ``result.errors.append("push_leads_unmatched:<code>")``
    #          (row stays dispatching; sweeper resets on next tick)
    #      * mark_send_failed returns matched=False → WARNING +
    #          ``result.errors.append("mark_send_failed_noop:<code>")``
    await _handle_per_lead_errors(
        push_result=push_result,
        message_ids=message_ids,
        emails_by_msg_id=emails_by_msg_id,
        msg_repo=msg_repo,
        result=result,
    )


async def _handle_per_lead_errors(
    *,
    push_result: Any,
    message_ids: dict[str, str],
    emails_by_msg_id: dict[str, str],
    msg_repo: Any,
    result: TickResult,
) -> None:
    """Issue #367 step-7 telemetry. Always log raw push_leads error
    BEFORE reconciliation so operators see every failure regardless of
    whether the email → message_id lookup succeeds."""
    per_lead_errors = list(getattr(push_result, "errors", []) or [])
    if not per_lead_errors:
        return

    # Reverse map: lower-cased email → unique_key. Casing differences
    # between dispatcher response and our claimed batch shouldn't break
    # reconciliation (previous code did a O(N) inner loop with case
    # match — equivalent, but the reverse map makes the fallback
    # discriminator below cheap).
    email_to_uk: dict[str, str] = {}
    for uk, mid in message_ids.items():
        e = emails_by_msg_id.get(mid, "").strip().lower()
        if e:
            email_to_uk[e] = uk

    single_batch = len(message_ids) == 1 and len(per_lead_errors) == 1

    for err in per_lead_errors:
        err_email = (getattr(err, "email", "") or "").strip().lower()
        err_code = str(getattr(err, "error_code", "") or "unknown")
        err_msg = str(getattr(err, "error_message", "") or "")[:200]

        # ALWAYS log raw error first — operators see every failure even
        # when reconciliation drops the row downstream.
        logger.info(
            "dispatch_tick push_leads per-lead error",
            extra={
                "err_email": err_email,
                "error_code": err_code,
                "error_message": err_msg,
                "claimed_count": len(message_ids),
            },
        )

        uk_match = email_to_uk.get(err_email)
        used_fallback = False

        if not uk_match and single_batch:
            uk_match = next(iter(message_ids))
            used_fallback = True
            logger.warning(
                "dispatch_tick push_leads error email %r not in claimed "
                "batch; single-message fallback assigns to %s",
                err_email,
                uk_match,
                extra={
                    "err_email": err_email,
                    "claimed_emails": list(email_to_uk.keys()),
                    "fallback_uk": uk_match,
                    "error_code": err_code,
                },
            )

        if not uk_match:
            result.swallowed += 1
            result.errors.append(f"push_leads_unmatched:{err_code}")
            logger.warning(
                "dispatch_tick push_leads error for unknown email; "
                "cannot reconcile with claimed message_ids",
                extra={
                    "err_email": err_email,
                    "error_code": err_code,
                    "error_message": err_msg,
                    "claimed_emails": sorted(email_to_uk.keys()),
                },
            )
            continue

        msg_id = message_ids[uk_match]
        mark = await msg_repo.mark_send_failed(
            msg_id,
            error=f"{err_code}:{err_msg[:120]}",
        )
        if not mark.matched:
            result.errors.append(f"mark_send_failed_noop:{err_code}")
            logger.warning(
                "dispatch_tick mark_send_failed matched 0 rows for "
                "msg=%s — row may have changed state mid-tick",
                msg_id,
                extra={
                    "lds_message_id": msg_id,
                    "lead_unique_key": uk_match,
                    "error_code": err_code,
                    "mark_error": mark.error,
                    "used_fallback": used_fallback,
                },
            )
            continue

        logger.info(
            "dispatch_tick marked msg=%s bounced (send_failed)",
            msg_id,
            extra={
                "lds_message_id": msg_id,
                "lead_unique_key": uk_match,
                "recipient_email": err_email,
                "error_code": err_code,
                "used_fallback": used_fallback,
            },
        )


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
            msg_id,
            target_status,
        )


def _window_check_for_step(
    step: Any,
    *,
    lead_timezone: Optional[str] = None,
) -> tuple[bool, Optional[datetime]]:
    """Phase 15.3: window check uses the actual ``SequenceStep`` fields
    (PR #325 dataclass). Falls back to ``DispatchPolicy`` defaults if
    a field is empty (shouldn't happen given the DB NOT NULL defaults,
    but defensive).
    """
    from src.utils.dispatch_policy import DISPATCH_POLICY
    from src.utils.send_window import is_within_window

    check = is_within_window(
        step_send_window_start=getattr(step, "send_window_start", None)
        or DISPATCH_POLICY.send_window_start,
        step_send_window_end=getattr(step, "send_window_end", None)
        or DISPATCH_POLICY.send_window_end,
        step_send_days=getattr(step, "send_days", None)
        or ",".join(DISPATCH_POLICY.send_days),
        timezone_name=lead_timezone,
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
