"""Cooperative-cancel race-condition test for ParallelAuditor.

Catches the B9 race documented in src/core/task_orchestrator.py:410-422:
when /orchestrator/stop is called mid-run, every lead in the batch must end
in an atomic terminal state — either fully audited (audit_status='Completed'
+ audit_results + seo_score) or untouched (initial seed value with no
audit_results / no seo_score) — never a torn write.

Live integration test. Requires a running FastAPI backend AND a real
Supabase project — fixtures are inserted via service-role and cleaned up
in tearDown. Skips when the env block is missing so CI without secrets
doesn't fail.

Required env:
  BACKEND_BASE_URL              FastAPI URL, default http://127.0.0.1:8000
  API_SECRET_KEY                backend secret (same as /backend/.env)
  SUPABASE_URL                  same as backend env
  SUPABASE_SERVICE_ROLE_KEY     service-role key, RLS-bypassing
"""

from __future__ import annotations

import os
import sys
import time
import unittest
import uuid
from typing import Any, Dict, List, Optional

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supabase import create_client  # noqa: E402  (path insertion needed first)


BACKEND_BASE_URL = (os.environ.get("BACKEND_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
API_SECRET_KEY = os.environ.get("API_SECRET_KEY") or ""
SUPABASE_URL = os.environ.get("SUPABASE_URL") or ""
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""

MISSING_ENV = [
    name for name, val in (
        ("API_SECRET_KEY", API_SECRET_KEY),
        ("SUPABASE_URL", SUPABASE_URL),
        ("SUPABASE_SERVICE_ROLE_KEY", SUPABASE_SERVICE_ROLE_KEY),
    ) if not val
]

LEAD_COUNT = 50
# Stable, fast, publicly resolvable URL — passes the SSRF guard and gives
# the auditor a real but quick page to score. All 50 fixture rows point at
# the same host so the test doesn't depend on a curated URL list.
FIXTURE_WEBSITE = "https://example.com"
FIXTURE_PREFIX = "e2e-cancel"

PRE_STOP_DELAY_SEC = 3.0
STOP_POLL_TIMEOUT_SEC = 5 * 60
STOP_POLL_INTERVAL_SEC = 2.0
DRAIN_STABLE_POLLS = 3
DRAIN_GRACE_SEC = 5.0
JOB_OVERALL_TIMEOUT_SEC = 10 * 60

# Columns that prove a row was modified by the audit pipeline. If ANY of
# these is non-null on a row whose audit_status is still the seeded
# "Pending", we have a torn write.
AUDIT_OUTPUT_COLS = (
    "audit_results", "seo_score", "high_risk_flag",
    "pain_points", "linkedin_hook", "email_hook",
)


@pytest.mark.live
@unittest.skipIf(MISSING_ENV, f"Missing live-env: {', '.join(MISSING_ENV)}")
class TestOrchestratorCooperativeCancel(unittest.TestCase):
    """One test. Long-running. Pre-conditions all live."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.db = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        cls.session = requests.Session()
        cls.session.headers.update({"X-API-Key": API_SECRET_KEY, "Content-Type": "application/json"})
        cls.fixture_keys: List[str] = []
        cls.job_id: Optional[str] = None

    @classmethod
    def tearDownClass(cls) -> None:
        # Defensive: always wipe the fixture rows even if assertions failed
        # midway. We seeded them, we own them.
        if cls.fixture_keys:
            try:
                cls.db.table("leads").delete().in_("unique_key", cls.fixture_keys).execute()
            except Exception as exc:  # noqa: BLE001 — cleanup must not raise
                sys.stderr.write(f"cleanup of {len(cls.fixture_keys)} fixture leads failed: {exc}\n")
        cls.session.close()

    # ------------------------------------------------------------------ helpers

    def _seed_leads(self) -> List[Dict[str, Any]]:
        """Insert LEAD_COUNT fixture rows and return them as upserted by Supabase."""
        nonce = uuid.uuid4().hex[:8]
        rows = [
            {
                "unique_key": f"{FIXTURE_PREFIX}-{nonce}-{i:03d}",
                "name": f"E2E Cancel Fixture {i:03d}",
                "company_name": f"Fixture Co {i:03d}",
                "website": FIXTURE_WEBSITE,
                "audit_status": "Pending",
                "lead_source": "e2e_fixture",
                "retry_count": 0,
            }
            for i in range(LEAD_COUNT)
        ]
        # upsert (not insert) so a re-run after a partial earlier seed is idempotent.
        res = self.db.table("leads").upsert(rows, on_conflict="unique_key").execute()
        self.assertEqual(len(res.data or []), LEAD_COUNT, "seed insert must return all rows")
        type(self).fixture_keys = [r["unique_key"] for r in rows]
        return rows

    def _start_job(self) -> str:
        payload = {"lead_ids": type(self).fixture_keys, "tasks": ["audit"]}
        resp = self.session.post(f"{BACKEND_BASE_URL}/orchestrator/start", json=payload, timeout=30)
        self.assertEqual(resp.status_code, 200, f"start returned {resp.status_code}: {resp.text}")
        body = resp.json()
        self.assertEqual(body.get("status"), "job_started", body)
        job_id = body.get("job_id")
        self.assertTrue(job_id, "job_id must be returned")
        return job_id

    def _stop_job(self, job_id: str) -> None:
        resp = self.session.post(f"{BACKEND_BASE_URL}/orchestrator/stop/{job_id}", timeout=30)
        self.assertEqual(resp.status_code, 200, f"stop returned {resp.status_code}: {resp.text}")
        body = resp.json()
        self.assertIn(body.get("status"), {"stopping", "not_found"}, body)

    def _poll_job_status(self, job_id: str) -> Dict[str, Any]:
        resp = self.session.get(f"{BACKEND_BASE_URL}/orchestrator/status/{job_id}", timeout=30)
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _count_touched_fixtures(self) -> int:
        """Authoritative drain signal: count fixture rows whose
        last_processed_at is set. The Failed branch of `_process_single_lead`
        keeps audit_status='Pending' until retry_count >= 3, so watching
        audit_status alone misses retry-pending rows. last_processed_at is
        set on every touched row (success AND failure paths) and stays NULL
        on cancelled-mid-flight rows. processed_count on the job only ticks
        once-per-chunk so it's useless for a 1-chunk run.
        Done client-side so we don't depend on a specific postgrest-py
        version's `not.is null` filter syntax."""
        keys = type(self).fixture_keys
        touched = 0
        for chunk_start in range(0, len(keys), 25):
            slice_keys = keys[chunk_start : chunk_start + 25]
            res = (
                self.db.table("leads")
                .select("unique_key, last_processed_at")
                .in_("unique_key", slice_keys)
                .execute()
            )
            for r in (res.data or []):
                if r.get("last_processed_at"):
                    touched += 1
        return touched

    def _wait_for_stopped(self, job_id: str) -> Dict[str, Any]:
        """Wait until the job is status=stopped AND fixture-row state is
        stable for DRAIN_STABLE_POLLS consecutive polls (the active chunk's
        gather + upsert has finished and no new rows are landing). Running
        invariant checks before the in-flight chunk drains would race the
        test against the very thing it's testing."""
        deadline = time.monotonic() + STOP_POLL_TIMEOUT_SEC
        last_non_pending: Optional[int] = None
        stable_streak = 0
        stopped_seen = False
        last_status: Dict[str, Any] = {}
        while time.monotonic() < deadline:
            status = self._poll_job_status(job_id)
            last_status = status
            st = status.get("status")
            if st in ("stopped", "failed", "completed"):
                stopped_seen = True
            if stopped_seen:
                touched = self._count_touched_fixtures()
                if touched == last_non_pending:
                    stable_streak += 1
                    if stable_streak >= DRAIN_STABLE_POLLS:
                        time.sleep(DRAIN_GRACE_SEC)
                        return status
                else:
                    stable_streak = 0
                    last_non_pending = touched
            time.sleep(STOP_POLL_INTERVAL_SEC)
        self.fail(f"job {job_id} did not drain within {STOP_POLL_TIMEOUT_SEC}s (last status={last_status})")

    def _fetch_all_fixture_rows(self) -> List[Dict[str, Any]]:
        # Paginate defensively in case PostgREST default limit < 50.
        rows: List[Dict[str, Any]] = []
        for chunk_start in range(0, len(type(self).fixture_keys), 25):
            slice_keys = type(self).fixture_keys[chunk_start : chunk_start + 25]
            res = self.db.table("leads").select("*").in_("unique_key", slice_keys).execute()
            rows.extend(res.data or [])
        return rows

    # ------------------------------------------------------------------ test

    def test_no_partial_writes_on_cooperative_cancel(self) -> None:
        # 1. Seed 50 leads (audit_status='Pending', all audit_outputs NULL).
        seeded = self._seed_leads()
        seeded_by_key = {r["unique_key"]: r for r in seeded}

        # Snapshot the pre-start DB state (we'll diff against this).
        pre_rows = self._fetch_all_fixture_rows()
        self.assertEqual(len(pre_rows), LEAD_COUNT)
        for row in pre_rows:
            self.assertEqual(row.get("audit_status"), "Pending")
            for col in AUDIT_OUTPUT_COLS:
                self.assertIsNone(
                    row.get(col),
                    f"pre-test row {row['unique_key']} already has {col}={row.get(col)!r}",
                )

        # 2. Start the audit pipeline.
        overall_deadline = time.monotonic() + JOB_OVERALL_TIMEOUT_SEC
        type(self).job_id = self._start_job()
        job_id = type(self).job_id

        # 3. Let the auditor get its hands dirty, then stop.
        time.sleep(PRE_STOP_DELAY_SEC)
        self._stop_job(job_id)

        # 4. Wait for status='stopped' AND a stable processed_count — i.e.
        # the in-flight chunk's gather has resolved and the upsert has flushed.
        final_status = self._wait_for_stopped(job_id)
        self.assertIn(
            final_status.get("status"),
            {"stopped", "completed", "failed"},
            f"job must reach a terminal status, got {final_status}",
        )
        # Honest defense: if the whole pipeline finished before our 3s sleep
        # even hit, the cancel-race surface wasn't exercised. Flag it instead
        # of silently passing.
        if final_status.get("status") == "completed":
            self.fail(
                "job completed before stop took effect — increase LEAD_COUNT "
                "or use a slower fixture website to actually exercise the "
                "cooperative-cancel checkpoints",
            )
        self.assertLess(time.monotonic(), overall_deadline, "overall timeout tripped")

        # 5+6. Atomicity invariant. Each row must be in exactly one valid state:
        #   A) Untouched: audit_status == seeded value AND every audit output NULL.
        #   B) Completed: audit_status == 'Completed' AND audit_results NOT NULL
        #                 AND seo_score in [0, 100].
        #   C) Failed:    audit_status == 'Failed' AND last_error NOT NULL.
        # Any other shape is a torn write.
        post_rows = self._fetch_all_fixture_rows()
        self.assertEqual(len(post_rows), LEAD_COUNT, "all fixture rows must still exist")
        post_by_key = {r["unique_key"]: r for r in post_rows}

        untouched = completed = failed = 0
        violations: List[str] = []

        for key, row in post_by_key.items():
            pre = seeded_by_key[key]
            audit_status = row.get("audit_status")
            audit_results = row.get("audit_results")
            seo_score = row.get("seo_score")
            last_error = row.get("last_error")

            outputs_all_null = all(row.get(col) in (None, [], {}) for col in AUDIT_OUTPUT_COLS)

            if audit_status == pre.get("audit_status") and outputs_all_null:
                # State A — untouched.
                untouched += 1
                continue

            if audit_status == "Completed":
                # State B — full audit. ALL the load-bearing outputs must be set.
                if audit_results is None:
                    violations.append(f"{key}: Completed but audit_results IS NULL (torn write)")
                    continue
                if seo_score is None:
                    violations.append(f"{key}: Completed but seo_score IS NULL (torn write)")
                    continue
                try:
                    score = float(seo_score)
                except (TypeError, ValueError):
                    violations.append(f"{key}: seo_score is non-numeric {seo_score!r}")
                    continue
                if not 0 <= score <= 100:
                    violations.append(f"{key}: seo_score={score} outside [0,100]")
                    continue
                completed += 1
                continue

            if audit_status == "Failed":
                # State C — failed audit (terminal). The pipeline writes
                # last_error on the exception path; if it's missing, the
                # Failed status was set by some other code path that didn't
                # follow the contract.
                if not last_error:
                    violations.append(f"{key}: Failed but last_error is empty — non-atomic Failed write")
                    continue
                failed += 1
                continue

            # Anything else: a row that left "Pending" without reaching a
            # terminal state, OR one whose status is some intermediate
            # value (e.g. "In Progress"). Either is a torn write.
            violations.append(
                f"{key}: indeterminate state audit_status={audit_status!r} "
                f"audit_results={'set' if audit_results else 'null'} "
                f"seo_score={seo_score!r} last_error={'set' if last_error else 'null'}"
            )

        # Also guard against any audit-output column being set on a row
        # whose audit_status is still 'Pending' — a torn shape the loop
        # above ignores because it short-circuits on the all-null check.
        for key, row in post_by_key.items():
            if row.get("audit_status") == "Pending":
                for col in AUDIT_OUTPUT_COLS:
                    if row.get(col) not in (None, [], {}):
                        violations.append(
                            f"{key}: audit_status=Pending but {col}={row.get(col)!r} (torn write)"
                        )

        self.assertFalse(
            violations,
            "Cooperative cancel produced torn writes:\n  " + "\n  ".join(violations),
        )

        # And the pipeline must have made *some* progress (else the test is
        # vacuous and the assertion is just rubber-stamping a no-op).
        self.assertGreater(
            untouched + completed + failed,
            0,
            "categorisation accounted for no rows — assertion logic is wrong",
        )
        self.assertEqual(
            untouched + completed + failed,
            LEAD_COUNT,
            f"row accounting mismatch: untouched={untouched} completed={completed} "
            f"failed={failed} total={LEAD_COUNT}",
        )

        # Print a small breakdown — useful when this runs in CI and someone
        # wants to see whether the cancel actually caught the auditor mid-flight.
        print(
            f"\n[cooperative-cancel] untouched={untouched} "
            f"completed={completed} failed={failed} (of {LEAD_COUNT})",
        )


if __name__ == "__main__":
    unittest.main()
