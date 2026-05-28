"""Unit tests for src/workers/dispatch_tick.py + the claim/sweep
methods on CampaignMessageRepository (added in Phase 15.2).

Critical coverage:
- claim_due_batch happy path: SELECT due + UPDATE pending→dispatching
- claim_due_batch concurrent: two ticks SELECT same ids; only first
  UPDATE matches (status='pending' predicate gates the second)
- sweep_stale_claims: dispatching rows past timeout → reset to pending
- run_tick: full pipeline metrics (no due → 0 dispatched)
- run_tick: out-of-window → release claim back to pending
- run_tick: post-schedule suppression → release as 'cancelled'
- run_tick: runtime cap honoured
- run_tick: dispatcher unavailable → 'dispatcher_unavailable' error
"""
from __future__ import annotations

import asyncio
import os
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from src.repositories.campaign_message_repo import CampaignMessageRepository


def _build_db(
    fetch_due_rows: list[dict] | None = None,
    update_returns: list[dict] | None = None,
) -> tuple[Any, MagicMock, dict[str, Any]]:
    """Recording supabase-py mock.

    Captures every update/insert call so tests can assert state-transition
    semantics. Returns (client, table, captures) — captures dict has
    'updates' list of (set, where) tuples + 'fetch_due_returned'.
    """
    table = MagicMock(name="table")
    table._where: dict[str, Any] = {}
    table._set: dict[str, Any] = {}

    captures: dict[str, Any] = {
        "updates": [],
        "selects": [],
    }

    table.select.return_value = table
    table.update.side_effect = lambda values, t=table: (
        setattr(t, "_set", dict(values)) or t
    )
    table.eq.side_effect = lambda col, val, t=table: (
        t._where.__setitem__(col, val) or t
    )
    table.in_.side_effect = lambda col, vals, t=table: (
        t._where.__setitem__(f"{col}__in", list(vals)) or t
    )
    table.lt.side_effect = lambda col, val, t=table: (
        t._where.__setitem__(f"{col}__lt", val) or t
    )
    table.lte.side_effect = lambda col, val, t=table: (
        t._where.__setitem__(f"{col}__lte", val) or t
    )
    table.order.return_value = table
    table.limit.return_value = table

    # Counter to alternate between fetch_due response (1st execute on a
    # chained call that hit .lte+.order+.limit) and update response
    # (subsequent execute after .update().in_().eq()).
    state = {"calls": 0}

    def execute():
        state["calls"] += 1
        # First call w/ set populated → it's an UPDATE.
        if table._set:
            captures["updates"].append((dict(table._set), dict(table._where)))
            data = list(update_returns or [])
            table._set = {}
            table._where = {}
            return MagicMock(data=data)
        # Otherwise it's a SELECT (fetch_due path).
        data = list(fetch_due_rows or [])
        captures["selects"].append(dict(table._where))
        table._where = {}
        return MagicMock(data=data)

    table.execute.side_effect = execute

    client = MagicMock(name="client")
    client.table.return_value = table
    return client, table, captures


# ---------------------------------------------------------------------------
# CampaignMessageRepository.claim_due_batch
# ---------------------------------------------------------------------------


class TestClaimDueBatch(unittest.TestCase):
    def test_happy_path_select_then_update(self) -> None:
        due = [
            {"id": "msg-1", "lead_unique_key": "lead-1"},
            {"id": "msg-2", "lead_unique_key": "lead-2"},
        ]
        # Mock the UPDATE returning both rows (== both claimed).
        client, table, captures = _build_db(
            fetch_due_rows=due, update_returns=due,
        )
        repo = CampaignMessageRepository(client)
        result = asyncio.run(repo.claim_due_batch(limit=10, now_iso="2026-05-26T10:00:00+00:00"))
        self.assertEqual(len(result), 2)
        # Exactly one UPDATE captured: SET status='dispatching', dispatched_at,
        # WHERE id IN [msg-1, msg-2] AND status='pending'.
        self.assertEqual(len(captures["updates"]), 1)
        set_clause, where = captures["updates"][0]
        self.assertEqual(set_clause["status"], "dispatching")
        self.assertEqual(set_clause["dispatched_at"], "2026-05-26T10:00:00+00:00")
        self.assertEqual(set(where["id__in"]), {"msg-1", "msg-2"})
        self.assertEqual(where["status"], "pending")

    def test_loser_tick_matches_zero_rows(self) -> None:
        """Real PostgREST: predicate status='pending' on rows already
        flipped to 'dispatching' by a winner tick → UPDATE returns 0
        rows. Repo translates that to an empty claim list (NOT an
        error)."""
        due = [{"id": "msg-1"}]
        # UPDATE returns 0 rows (already-claimed by winner).
        client, _, captures = _build_db(
            fetch_due_rows=due, update_returns=[],
        )
        repo = CampaignMessageRepository(client)
        result = asyncio.run(repo.claim_due_batch(limit=10))
        self.assertEqual(result, [])
        # But the UPDATE was still ATTEMPTED — that's the contract.
        self.assertEqual(len(captures["updates"]), 1)

    def test_no_due_messages_short_circuits_no_update(self) -> None:
        client, _, captures = _build_db(fetch_due_rows=[])
        repo = CampaignMessageRepository(client)
        result = asyncio.run(repo.claim_due_batch(limit=10))
        self.assertEqual(result, [])
        # No UPDATE issued — nothing to claim.
        self.assertEqual(len(captures["updates"]), 0)


# ---------------------------------------------------------------------------
# CampaignMessageRepository.sweep_stale_claims
# ---------------------------------------------------------------------------


class TestSweepStaleClaims(unittest.TestCase):
    def test_sweeps_stuck_rows_back_to_pending(self) -> None:
        stale = [{"id": "msg-stuck-1"}, {"id": "msg-stuck-2"}]
        client, _, captures = _build_db(update_returns=stale)
        repo = CampaignMessageRepository(client)
        result = asyncio.run(repo.sweep_stale_claims(
            timeout_minutes=15, now_iso="2026-05-26T10:00:00+00:00",
        ))
        self.assertEqual(result, 2)
        self.assertEqual(len(captures["updates"]), 1)
        set_clause, where = captures["updates"][0]
        self.assertEqual(set_clause["status"], "pending")
        self.assertIsNone(set_clause["dispatched_at"])
        self.assertEqual(where["status"], "dispatching")
        # Cutoff = now - 15min = 09:45:00 UTC.
        self.assertEqual(where["dispatched_at__lt"], "2026-05-26T09:45:00+00:00")

    def test_zero_timeout_no_op(self) -> None:
        client, _, captures = _build_db()
        repo = CampaignMessageRepository(client)
        result = asyncio.run(repo.sweep_stale_claims(timeout_minutes=0))
        self.assertEqual(result, 0)
        # No UPDATE issued.
        self.assertEqual(len(captures["updates"]), 0)


# ---------------------------------------------------------------------------
# run_tick end-to-end metrics
# ---------------------------------------------------------------------------


class TestRunTick(unittest.TestCase):
    def setUp(self) -> None:
        # All run_tick tests build their own client / dispatcher mocks
        # so the worker has explicit injection rather than env wiring.
        os.environ.pop("DISPATCH_TICK_BATCH_SIZE", None)
        os.environ.pop("DISPATCH_CLAIM_TIMEOUT_MIN", None)

    def test_no_due_messages_returns_zero_dispatched(self) -> None:
        from src.workers.dispatch_tick import run_tick

        client, _, _ = _build_db(fetch_due_rows=[], update_returns=[])
        dispatcher = MagicMock()  # Never called.

        result = asyncio.run(run_tick(
            db_client=client, dispatcher=dispatcher,
            batch_size=10, claim_timeout_min=15, max_runtime_sec=10,
            now_iso="2026-05-26T10:00:00+00:00",
        ))
        self.assertEqual(result.claimed, 0)
        self.assertEqual(result.dispatched, 0)
        self.assertEqual(result.errors, [])
        dispatcher.push_leads.assert_not_called()

    def test_dispatcher_unavailable_errors_out(self) -> None:
        from src.workers.dispatch_tick import run_tick
        client, _, _ = _build_db(
            fetch_due_rows=[{"id": "msg-1", "lead_unique_key": "uk-1",
                            "recipient_email": "r@x.com"}],
            update_returns=[{"id": "msg-1", "lead_unique_key": "uk-1",
                            "recipient_email": "r@x.com",
                            "scheduled_at": "2026-05-26T10:00:00Z"}],
        )

        with patch("src.workers.dispatch_tick._resolve_dispatcher", return_value=None):
            result = asyncio.run(run_tick(
                db_client=client, dispatcher=None,
                batch_size=10, claim_timeout_min=15, max_runtime_sec=10,
                now_iso="2026-05-26T10:00:00+00:00",
            ))
        # Tue 10:00 UTC matches default Mon-Fri 09-17 window → eligible.
        # Dispatcher fails to resolve → tick records the error.
        self.assertIn("dispatcher_unavailable", result.errors)
        self.assertEqual(result.dispatched, 0)


# ---------------------------------------------------------------------------
# Issue #367 — per-lead error fan-out telemetry
# ---------------------------------------------------------------------------


def _aret(value):
    """Wrap a value in an async-returning callable for mock side_effect."""
    async def _f(*a, **kw):
        return value
    return _f


def _araise(exc):
    """Wrap an exception in an async-raising callable for mock side_effect."""
    async def _f(*a, **kw):
        raise exc
    return _f


class TestHandlePerLeadErrors(unittest.IsolatedAsyncioTestCase):
    """Issue #367: per-lead error fan-out emits distinct telemetry
    buckets — matched, single-batch fallback, unmatched (swallowed),
    mark_send_failed no-op. None of them get merged into a single
    catch-all counter."""

    async def test_matched_email_marks_send_failed(self) -> None:
        """Standard path: err.email matches a claimed message →
        mark_send_failed called with concatenated error code+message;
        swallowed counter stays 0 and result.errors stays empty."""
        from src.workers.dispatch_tick import (
            TickResult, _handle_per_lead_errors,
        )
        from src.repositories.campaign_message_repo import MarkResult

        push_result = MagicMock()
        push_result.errors = [
            MagicMock(
                email="a@x.com",
                error_code="http_400",
                error_message="invalid recipient",
            ),
        ]
        message_ids = {"uk-1": "msg-1", "uk-2": "msg-2"}
        emails = {"msg-1": "a@x.com", "msg-2": "b@x.com"}

        msg_repo = MagicMock()
        msg_repo.mark_send_failed = MagicMock(
            side_effect=_aret(MarkResult(matched=True)),
        )

        result = TickResult()
        await _handle_per_lead_errors(
            push_result=push_result,
            message_ids=message_ids,
            emails_by_msg_id=emails,
            msg_repo=msg_repo,
            result=result,
        )

        msg_repo.mark_send_failed.assert_called_once_with(
            "msg-1", error="http_400:invalid recipient",
        )
        self.assertEqual(result.swallowed, 0)
        self.assertEqual(result.errors, [])

    async def test_unmatched_multi_batch_counts_swallowed(self) -> None:
        """Multi-claim batch + error with unknown email →
        ``result.swallowed += 1`` AND ``result.errors`` gets a
        ``push_leads_unmatched:<code>`` entry. mark_send_failed is
        NOT called (we don't know which row failed). Distinct from
        any other failure bucket. WARNING surfaces the orphaned
        claim so operators see it without a wrapper script."""
        from src.workers.dispatch_tick import (
            TickResult, _handle_per_lead_errors,
        )

        push_result = MagicMock()
        push_result.errors = [
            MagicMock(
                email="ghost@nowhere.com",
                error_code="http_422",
                error_message="validation failed",
            ),
        ]
        message_ids = {"uk-1": "msg-1", "uk-2": "msg-2"}
        emails = {"msg-1": "a@x.com", "msg-2": "b@x.com"}

        msg_repo = MagicMock()
        msg_repo.mark_send_failed = MagicMock()

        result = TickResult()
        with self.assertLogs(
            "src.workers.dispatch_tick", level="WARNING",
        ) as logs:
            await _handle_per_lead_errors(
                push_result=push_result,
                message_ids=message_ids,
                emails_by_msg_id=emails,
                msg_repo=msg_repo,
                result=result,
            )

        msg_repo.mark_send_failed.assert_not_called()
        self.assertEqual(result.swallowed, 1)
        self.assertEqual(result.errors, ["push_leads_unmatched:http_422"])
        self.assertTrue(
            any(
                "unknown email" in r.getMessage()
                for r in logs.records
            ),
            msg=f"records={[r.getMessage() for r in logs.records]}",
        )

    async def test_unmatched_single_batch_fallback_marks(self) -> None:
        """Single claim + single error with unknown email →
        fallback assigns the error to the lone claimed msg,
        mark_send_failed IS called, swallowed stays 0, WARNING
        logged about the fallback. Mirrors the Issue #367 evidence
        scenario."""
        from src.workers.dispatch_tick import (
            TickResult, _handle_per_lead_errors,
        )
        from src.repositories.campaign_message_repo import MarkResult

        push_result = MagicMock()
        push_result.errors = [
            MagicMock(
                email="DIFFERENT@x.com",
                error_code="http_402",
                error_message="paid plan required",
            ),
        ]
        message_ids = {"uk-1": "msg-1"}
        emails = {"msg-1": "actual@x.com"}

        msg_repo = MagicMock()
        msg_repo.mark_send_failed = MagicMock(
            side_effect=_aret(MarkResult(matched=True)),
        )

        result = TickResult()
        with self.assertLogs(
            "src.workers.dispatch_tick", level="WARNING",
        ) as logs:
            await _handle_per_lead_errors(
                push_result=push_result,
                message_ids=message_ids,
                emails_by_msg_id=emails,
                msg_repo=msg_repo,
                result=result,
            )

        msg_repo.mark_send_failed.assert_called_once_with(
            "msg-1", error="http_402:paid plan required",
        )
        self.assertEqual(result.swallowed, 0)
        self.assertEqual(result.errors, [])
        self.assertTrue(
            any(
                "single-message fallback" in r.getMessage()
                for r in logs.records
            ),
            msg=f"records={[r.getMessage() for r in logs.records]}",
        )

    async def test_mark_send_failed_no_match_distinct_bucket(self) -> None:
        """Matched email but ``mark_send_failed`` returns
        ``matched=False`` (row already transitioned mid-tick) →
        ``result.errors`` gets ``mark_send_failed_noop:<code>``,
        DISTINCT from ``push_leads_unmatched``. ``swallowed`` stays
        0 — we DID find the row, just couldn't update it."""
        from src.workers.dispatch_tick import (
            TickResult, _handle_per_lead_errors,
        )
        from src.repositories.campaign_message_repo import MarkResult

        push_result = MagicMock()
        push_result.errors = [
            MagicMock(
                email="a@x.com",
                error_code="http_429",
                error_message="rate limit",
            ),
        ]
        message_ids = {"uk-1": "msg-1"}
        emails = {"msg-1": "a@x.com"}

        msg_repo = MagicMock()
        msg_repo.mark_send_failed = MagicMock(
            side_effect=_aret(
                MarkResult(matched=False, error="not_dispatching"),
            ),
        )

        result = TickResult()
        await _handle_per_lead_errors(
            push_result=push_result,
            message_ids=message_ids,
            emails_by_msg_id=emails,
            msg_repo=msg_repo,
            result=result,
        )

        self.assertEqual(result.swallowed, 0)
        self.assertEqual(result.errors, ["mark_send_failed_noop:http_429"])

    async def test_no_errors_no_op(self) -> None:
        """``push_result.errors`` empty → no DB calls, no log lines,
        no result mutations."""
        from src.workers.dispatch_tick import (
            TickResult, _handle_per_lead_errors,
        )

        push_result = MagicMock()
        push_result.errors = []

        msg_repo = MagicMock()
        msg_repo.mark_send_failed = MagicMock()

        result = TickResult()
        await _handle_per_lead_errors(
            push_result=push_result,
            message_ids={"uk-1": "msg-1"},
            emails_by_msg_id={"msg-1": "a@x.com"},
            msg_repo=msg_repo,
            result=result,
        )
        msg_repo.mark_send_failed.assert_not_called()
        self.assertEqual(result.swallowed, 0)
        self.assertEqual(result.errors, [])


class TestRunTickExceptionPath(unittest.IsolatedAsyncioTestCase):
    """Issue #367: push_leads RAISES (network/transport) →
    ``dispatch_failed:X`` bucket in ``result.errors``, ``swallowed``
    stays 0, per-lead fan-out NOT reached so ``mark_send_failed``
    never called."""

    async def test_push_leads_raise_dispatch_failed_distinct_from_swallowed(
        self,
    ) -> None:
        from src.workers.dispatch_tick import run_tick

        claim_row = {
            "id": "msg-1",
            "lead_unique_key": "uk-1",
            "step_id": "step-1",
            "tracking_id": "trk-1",
        }

        fake_msg_repo = MagicMock()
        fake_msg_repo.sweep_stale_claims = MagicMock(side_effect=_aret(0))
        fake_msg_repo.claim_due_batch = MagicMock(side_effect=_aret([claim_row]))
        fake_msg_repo.fetch_many = MagicMock(side_effect=_aret({}))
        fake_msg_repo.mark_send_failed = MagicMock()

        fake_lead_repo = MagicMock()
        fake_lead_repo.fetch_many = MagicMock(
            side_effect=_aret(
                {"uk-1": {"email": "a@x.com", "timezone": "UTC"}},
            ),
        )

        step_obj = MagicMock()
        step_obj.send_window_start = "09:00"
        step_obj.send_window_end = "17:00"
        step_obj.send_days = "mon,tue,wed,thu,fri,sat,sun"
        fake_step_repo = MagicMock()
        fake_step_repo.fetch_many = MagicMock(
            side_effect=_aret({"step-1": step_obj}),
        )

        var_obj = MagicMock(content_type="text")
        fake_variant_repo = MagicMock()
        fake_variant_repo.fetch_many_for_steps = MagicMock(
            side_effect=_aret({"step-1": [var_obj]}),
        )

        fake_sup_repo = MagicMock()
        fake_sup_repo.filter_suppressed = MagicMock(
            side_effect=_aret(([], [])),
        )

        payload = MagicMock()
        payload.as_lead_dict.return_value = {"email": "a@x.com"}
        payload.list_unsubscribe_url = ""

        dispatcher = MagicMock()
        dispatcher.push_leads = MagicMock(
            side_effect=_araise(RuntimeError("boom")),
        )

        with patch(
            "src.repositories.campaign_message_repo.CampaignMessageRepository",
            return_value=fake_msg_repo,
        ), patch(
            "src.repositories.suppression_repo.SuppressionRepository",
            return_value=fake_sup_repo,
        ), patch(
            "src.repositories.lead_repo.LeadRepository",
            return_value=fake_lead_repo,
        ), patch(
            "src.repositories.sequence_step_repo.SequenceStepRepository",
            return_value=fake_step_repo,
        ), patch(
            "src.repositories.sequence_variant_repo.SequenceVariantRepository",
            return_value=fake_variant_repo,
        ), patch(
            "src.workers.dispatch_tick._window_check_for_step",
            return_value=(True, None),
        ), patch(
            "src.services.variant_selector.select_variant",
            return_value=var_obj,
        ), patch(
            "src.services.thread_builder.build_send_payload",
            return_value=payload,
        ), patch(
            "src.utils.unsubscribe_tokens.build_unsubscribe_url",
            return_value="",
        ):
            result = await run_tick(
                db_client=MagicMock(),
                dispatcher=dispatcher,
                batch_size=10,
                claim_timeout_min=15,
                max_runtime_sec=10,
                now_iso="2026-05-27T10:00:00+00:00",
            )

        # Exception path → distinct dispatch_failed:X bucket.
        self.assertTrue(
            any(e.startswith("dispatch_failed:") for e in result.errors),
            msg=f"expected dispatch_failed:* in errors, got {result.errors}",
        )
        # NOT merged into swallowed — that bucket only fills from
        # unmatched per-lead errors after a successful push_leads call.
        self.assertEqual(result.swallowed, 0)
        # Per-lead fan-out skipped on exception path.
        fake_msg_repo.mark_send_failed.assert_not_called()


class TestSummaryLogOnEveryReturn(unittest.IsolatedAsyncioTestCase):
    """Issue #367: ``dispatch_tick summary`` INFO log emitted on EVERY
    return path (try/finally), even early exits like
    ``db_client_unavailable``. Operator sees the tick outcome
    without instrumenting a wrapper script."""

    async def test_summary_log_emitted_on_db_unavailable_early_return(
        self,
    ) -> None:
        from src.workers.dispatch_tick import run_tick

        with patch(
            "src.workers.dispatch_tick._resolve_db_client",
            return_value=None,
        ):
            with self.assertLogs(
                "src.workers.dispatch_tick", level="INFO",
            ) as logs:
                result = await run_tick(db_client=None, dispatcher=None)

        self.assertIn("db_client_unavailable", result.errors)
        summary = [
            r for r in logs.records
            if r.getMessage() == "dispatch_tick summary"
        ]
        self.assertEqual(
            len(summary), 1,
            msg=f"expected 1 summary log, got {len(summary)}",
        )
        # Summary attaches result.as_dict() as record attributes via
        # ``extra=``; check the key fields propagated.
        rec = summary[0]
        self.assertEqual(getattr(rec, "claimed", None), 0)
        self.assertEqual(getattr(rec, "dispatched", None), 0)
        self.assertEqual(getattr(rec, "swallowed", None), 0)
        self.assertIn("db_client_unavailable", getattr(rec, "errors", []))


if __name__ == "__main__":
    unittest.main()
