"""Load test for LeadDataScraper FastAPI backend.

Three scenarios, selected via Locust --tags:

  A) read         GET /leads               50 users   5min   p95 < 500ms
  B) orchestrator POST /orchestrator/start 10 users   ~enqueue latency only
  C) stats        GET /stats               100 users  ~100 req/s sustained

Run (HTML report goes to ./reports/):

  export LOAD_API_BASE=https://leaddata-backend.onrender.com
  export LOAD_API_KEY=<API_SECRET_KEY value>

  # Scenario A
  locust -f tests/loadtest/locustfile.py --headless \\
         --tags read \\
         --users 50 --spawn-rate 10 --run-time 5m \\
         --host $LOAD_API_BASE \\
         --html tests/loadtest/reports/A_read.html \\
         --csv  tests/loadtest/reports/A_read

  # Scenario B
  locust -f tests/loadtest/locustfile.py --headless \\
         --tags orchestrator \\
         --users 10 --spawn-rate 2 --run-time 2m \\
         --host $LOAD_API_BASE \\
         --html tests/loadtest/reports/B_orchestrator.html \\
         --csv  tests/loadtest/reports/B_orchestrator

  # Scenario C (constant_throughput pins 1 rps per user → 100 users = 100 rps)
  locust -f tests/loadtest/locustfile.py --headless \\
         --tags stats \\
         --users 100 --spawn-rate 20 --run-time 3m \\
         --host $LOAD_API_BASE \\
         --html tests/loadtest/reports/C_stats.html \\
         --csv  tests/loadtest/reports/C_stats

Per-IP rate limits in backend/main.py block the target load from a single
source. _rate_limit_key honors X-Forwarded-For only when X-API-Key is valid,
so each virtual user injects a synthetic XFF to land in its own bucket.
This is a load-shape concession, not a production bypass — the same key+XFF
combination is what Vercel/Render proxies legitimately send.

──────────────────────────────────────────────────────────────────────────
asyncio.to_thread A/B (Scenario A only)
──────────────────────────────────────────────────────────────────────────
Baseline run is the supabase-sync code path: every /leads request blocks
the uvicorn worker until PostgREST replies, so concurrency ceiling ≈
worker count regardless of --users. Post-refactor, SupabaseHelper.
list_leads_recent and .get_stats_rows wrap the sync .execute() in
asyncio.to_thread, freeing the event loop to accept the next request
while the executor thread waits on the network.

To reproduce the A/B locally:

    # 1. baseline — revert just the touched files
    git stash push -m "to_thread" -- \\
        src/utils/supabase_helper.py \\
        backend/main.py \\
        src/core/task_orchestrator.py

    uvicorn backend.main:app --workers 2 --port 8000 &     # 2 workers ON PURPOSE
    locust -f tests/loadtest/locustfile.py --headless \\
           --tags read --users 50 --spawn-rate 10 --run-time 5m \\
           --host http://127.0.0.1:8000 \\
           --html tests/loadtest/reports/A_read_sync.html \\
           --csv  tests/loadtest/reports/A_read_sync
    kill %1

    # 2. refactored
    git stash pop
    uvicorn backend.main:app --workers 2 --port 8000 &
    locust -f tests/loadtest/locustfile.py --headless \\
           --tags read --users 50 --spawn-rate 10 --run-time 5m \\
           --host http://127.0.0.1:8000 \\
           --html tests/loadtest/reports/A_read_to_thread.html \\
           --csv  tests/loadtest/reports/A_read_to_thread
    kill %1

Expected: total RPS roughly 3-5× higher on the refactored run at the same
worker count; p95 drops from queue-bound (workers × per-request latency)
toward the underlying PostgREST round-trip floor. /stats Scenario C
benefits the same way; orchestrator Scenario B benefits less because the
synchronous critical path was already short (one insert).
"""

from __future__ import annotations

import os
import uuid
from typing import List, Optional

from locust import HttpUser, between, constant_throughput, events, tag, task


API_KEY_ENV = "LOAD_API_KEY"
API_KEY_HEADER = "X-API-Key"


# Each virtual user gets a synthetic IPv4 so slowapi's per-IP buckets don't
# collapse the target throughput. 10.x is RFC1918 and never collides with
# the platform-injected XFF in production.
def _synthetic_xff() -> str:
    raw = uuid.uuid4().int
    return f"10.{(raw >> 16) & 0xFF}.{(raw >> 8) & 0xFF}.{raw & 0xFF}"


class BaseAPIUser(HttpUser):
    abstract = True

    def on_start(self) -> None:
        api_key = os.environ.get(API_KEY_ENV)
        if not api_key:
            raise RuntimeError(
                f"{API_KEY_ENV} env var is required. Export the backend's "
                "API_SECRET_KEY value before invoking locust."
            )
        self.client.headers.update(
            {
                API_KEY_HEADER: api_key,
                "X-Forwarded-For": _synthetic_xff(),
                "Accept": "application/json",
            }
        )


class ReadUser(BaseAPIUser):
    """Scenario A — read pressure on the lead inventory endpoint."""

    wait_time = between(0.5, 1.5)

    @tag("read")
    @task
    def list_leads(self) -> None:
        with self.client.get("/leads", name="GET /leads", catch_response=True) as resp:
            if resp.status_code == 429:
                resp.failure("429 rate-limited (per-IP bucket saturated)")
            elif resp.status_code >= 500:
                resp.failure(f"5xx: {resp.status_code}")
            elif resp.status_code != 200:
                resp.failure(f"unexpected: {resp.status_code}")


class OrchestratorUser(BaseAPIUser):
    """Scenario B — 10 concurrent /orchestrator/start jobs, 20 leads each.

    Measures enqueue latency. The orchestrator dispatches background work
    via asyncio.create_task; the HTTP response returns the job_id
    immediately, so p95 here reflects the synchronous Supabase reads the
    handler performs before spawning, not the audit itself.
    """

    wait_time = between(2.0, 4.0)
    _lead_ids: Optional[List[str]] = None

    def on_start(self) -> None:
        super().on_start()
        self._lead_ids = self._fetch_lead_ids(limit=20) or [
            f"loadtest-{uuid.uuid4().hex[:16]}" for _ in range(20)
        ]

    def _fetch_lead_ids(self, limit: int) -> Optional[List[str]]:
        """Pull real unique_keys so the orchestrator has something to
        process. Falls back to synthetic IDs on any failure — the enqueue
        path is still exercised even if no row matches.
        """
        try:
            with self.client.get(
                "/leads",
                name="GET /leads (orchestrator bootstrap)",
                catch_response=True,
            ) as resp:
                if resp.status_code != 200:
                    return None
                payload = resp.json()
                rows = (
                    payload if isinstance(payload, list) else payload.get("leads", [])
                )
                ids = [
                    r["unique_key"]
                    for r in rows
                    if isinstance(r, dict) and r.get("unique_key")
                ]
                return ids[:limit] if ids else None
        except Exception:
            return None

    @tag("orchestrator")
    @task
    def start_pipeline(self) -> None:
        body = {"lead_ids": self._lead_ids, "tasks": ["audit"]}
        with self.client.post(
            "/orchestrator/start",
            json=body,
            name="POST /orchestrator/start",
            catch_response=True,
        ) as resp:
            if resp.status_code == 429:
                resp.failure("429 rate-limited (orchestrator bucket)")
            elif resp.status_code >= 500:
                resp.failure(f"5xx: {resp.status_code}")
            elif resp.status_code not in (200, 202):
                resp.failure(f"unexpected: {resp.status_code}")


class StatsUser(BaseAPIUser):
    """Scenario C — 100 rps on /stats (cache check).

    constant_throughput(1.0) pins each user to exactly one request per
    second regardless of response latency. Spawn 100 users → 100 rps.
    If the endpoint is uncached, p95 grows with row count because the
    handler does df = pd.DataFrame(rows) + value_counts per request.
    """

    wait_time = constant_throughput(1.0)

    @tag("stats")
    @task
    def stats(self) -> None:
        with self.client.get("/stats", name="GET /stats", catch_response=True) as resp:
            if resp.status_code == 429:
                resp.failure("429 rate-limited (no server-side cache)")
            elif resp.status_code >= 500:
                resp.failure(f"5xx: {resp.status_code}")
            elif resp.status_code != 200:
                resp.failure(f"unexpected: {resp.status_code}")


# Quick visibility on whether we're tripping rate limits vs hitting real
# backend errors. Locust's default stats already track 4xx/5xx counts,
# but emit a one-line console summary on shutdown for the report consumer.
@events.quitting.add_listener
def _print_targets(environment, **kwargs):  # noqa: ANN001
    stats = environment.stats.total
    p95 = stats.get_response_time_percentile(0.95)
    fail_pct = (
        (stats.num_failures / stats.num_requests * 100) if stats.num_requests else 0
    )
    print("\n=== Load test summary ===")
    print(f"requests : {stats.num_requests}")
    print(f"failures : {stats.num_failures} ({fail_pct:.2f}%)")
    print(f"p95 (ms) : {p95:.0f}")
    print(f"rps (avg): {stats.total_rps:.1f}")
    breaches = []
    if p95 > 500:
        breaches.append(f"p95 {p95:.0f}ms > 500ms target")
    if stats.num_failures > 0:
        breaches.append(f"{stats.num_failures} failures (target: 0 5xx)")
    if breaches:
        print("SLO breach: " + "; ".join(breaches))
    else:
        print("SLO met.")
