"""Concurrent-burst e2e test: rate limits hold, idempotency holds, no DB
corruption under contention.

Fires bursts of concurrent state-changing requests via `asyncio.gather` and
asserts:

1. /orchestrator/start  — `3/minute`. Burst of 20 → ≤ 3 succeed, the rest
   429; pre/post `orchestration_jobs` count grows by ≤ 3 (no double-insert
   under race).

2. /campaigns/{id}/start — `10/minute`. Burst of 20 against the SAME
   campaign → ≤ 10 succeed, the rest 429; campaign row ends as exactly
   one row with `status='active'` (UPDATE is idempotent).

3. /audit/stop — `10/minute`. Fired while ≥ 1 running job exists →
   2xx; afterwards no `orchestration_jobs.status='running'` rows remain
   (clean stop, no zombies).

4. DELETE /leads/clear ×10 — `3/hour`. ≤ 3 succeed, ≥ 7 return 429. This
   test wipes the DB; double-gated behind `ALLOW_DESTRUCTIVE_LEADS_CLEAR=1`.

Required env (`.env.test` / `.env` / process):
  RUN_CONCURRENCY_E2E=1
  BACKEND_URL=http://localhost:8000   (or staging URL)
  API_SECRET_KEY=<must match backend>
  ADMIN_TOKEN=<must match backend; needed for leads/clear>

Optional (for state assertions):
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Optional

import aiohttp
import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in [REPO_ROOT / ".env.test", REPO_ROOT / ".env"]:
        if path.exists():
            for k, v in dotenv_values(path).items():
                if v and k not in merged:
                    merged[k] = v
    for k in (
        "RUN_CONCURRENCY_E2E",
        "ALLOW_DESTRUCTIVE_LEADS_CLEAR",
        "BACKEND_URL",
        "API_SECRET_KEY",
        "ADMIN_TOKEN",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "NEXT_PUBLIC_SUPABASE_URL",
    ):
        v = os.environ.get(k)
        if v:
            merged[k] = v
    return merged


ENV = _load_env()

OPT_IN = ENV.get("RUN_CONCURRENCY_E2E", "").strip() in ("1", "true", "yes")
BACKEND_URL = (ENV.get("BACKEND_URL") or "").rstrip("/")
API_KEY = ENV.get("API_SECRET_KEY", "")
ADMIN_TOKEN = ENV.get("ADMIN_TOKEN", "")

pytestmark = [
    pytest.mark.skipif(
        not (OPT_IN and BACKEND_URL and API_KEY),
        reason=(
            "Set RUN_CONCURRENCY_E2E=1 + BACKEND_URL + API_SECRET_KEY "
            "to run the concurrent-burst e2e. Skipping."
        ),
    ),
    pytest.mark.asyncio,
]


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _service_role_client():
    url = ENV.get("SUPABASE_URL") or ENV.get("NEXT_PUBLIC_SUPABASE_URL")
    key = ENV.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        return None
    try:
        from supabase import create_client

        return create_client(url, key)
    except Exception:
        return None


def _table_count(svc, table: str) -> Optional[int]:
    if svc is None:
        return None
    try:
        r = svc.table(table).select("*", count="exact").limit(1).execute()
        return r.count or 0
    except Exception:
        return None


def _auth_headers(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    h = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


async def _post(
    session: aiohttp.ClientSession,
    path: str,
    *,
    method: str = "POST",
    json_body: Optional[dict] = None,
    extra_headers: Optional[dict[str, str]] = None,
) -> tuple[int, str]:
    url = f"{BACKEND_URL}/{path.lstrip('/')}"
    async with session.request(
        method,
        url,
        headers=_auth_headers(extra_headers),
        json=json_body,
        timeout=aiohttp.ClientTimeout(total=30),
    ) as r:
        text = await r.text()
        return r.status, text


def _classify(statuses: list[int]) -> tuple[int, int, int]:
    """Returns (succeeded_2xx, rate_limited_429, other)."""
    s2 = sum(1 for s in statuses if 200 <= s < 300)
    s429 = sum(1 for s in statuses if s == 429)
    other = len(statuses) - s2 - s429
    return s2, s429, other


# ---------------------------------------------------------------------------
# 1) /orchestrator/start — 3/minute, burst 20
# ---------------------------------------------------------------------------


async def test_orchestrator_start_burst_respects_rate_limit():
    svc = _service_role_client()
    before = _table_count(svc, "orchestration_jobs")

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *[
                _post(
                    session,
                    "orchestrator/start",
                    json_body={"task": "audit", "filters": "all"},
                )
                for _ in range(20)
            ]
        )

    statuses = [s for s, _ in results]
    ok, limited, other = _classify(statuses)

    # Limit is 3/minute. Allow exactly the limit window — anything beyond is
    # a regression (or an attempt that landed past `verify_api_key` raising).
    assert ok <= 3, (
        f"orchestrator/start: {ok} succeeded but limit is 3/min. Statuses: {statuses}"
    )
    assert limited >= 17, f"Expected ≥17 429s, got {limited}. Statuses: {statuses}"
    assert other == 0, f"Unexpected non-2xx/non-429 responses: {statuses}"

    # No DB corruption: job count rose by ≤ ok (each successful start
    # inserts at most one job row). The exact count may be < ok if the
    # handler short-circuits on missing config, but it must never exceed.
    if before is not None:
        await asyncio.sleep(2)  # let the background inserts land
        after = _table_count(svc, "orchestration_jobs")
        assert after is not None
        delta = after - before
        assert 0 <= delta <= ok, (
            f"orchestration_jobs delta={delta} (ok={ok}). Race may have "
            f"inserted duplicate rows."
        )


# ---------------------------------------------------------------------------
# 2) /campaigns/{id}/start — 10/minute, burst 20, idempotent
# ---------------------------------------------------------------------------


async def test_campaign_start_burst_idempotent():
    svc = _service_role_client()
    if svc is None:
        pytest.skip("service-role client required to create + verify the campaign row")

    # Create a throw-away campaign directly via service-role.
    campaign_id = str(uuid.uuid4())
    svc.table("campaigns").insert(
        {
            "id": campaign_id,
            "name": f"concurrency-test-{campaign_id[:8]}",
            "segment": "all",
            "status": "draft",
        }
    ).execute()

    try:
        async with aiohttp.ClientSession() as session:
            results = await asyncio.gather(
                *[_post(session, f"campaigns/{campaign_id}/start") for _ in range(20)]
            )

        statuses = [s for s, _ in results]
        ok, limited, other = _classify(statuses)

        assert ok <= 10, (
            f"campaigns/start: {ok} succeeded but limit is 10/min. Statuses: {statuses}"
        )
        assert limited >= 10, f"Expected ≥10 429s, got {limited}. Statuses: {statuses}"
        assert other == 0, f"Unexpected non-2xx/non-429 responses: {statuses}"

        # Idempotency: still exactly one row, still active.
        rows = (
            svc.table("campaigns")
            .select("id,status")
            .eq("id", campaign_id)
            .execute()
            .data
            or []
        )
        assert len(rows) == 1, f"Campaign row duplicated under race: {len(rows)} rows"
        assert rows[0]["status"] == "active", (
            f"Campaign status race-corrupted: {rows[0]['status']!r}"
        )

    finally:
        svc.table("campaigns").delete().eq("id", campaign_id).execute()


# ---------------------------------------------------------------------------
# 3) /audit/stop — clean stop, no zombie running jobs
# ---------------------------------------------------------------------------


async def test_audit_stop_leaves_no_running_jobs():
    svc = _service_role_client()
    if svc is None:
        pytest.skip("service-role client required to seed + verify orchestration_jobs")

    # Seed at least one 'running' job so /audit/stop has something to stop.
    seeded_job_id = str(uuid.uuid4())
    svc.table("orchestration_jobs").insert(
        {
            "id": seeded_job_id,
            "task_type": "audit",
            "status": "running",
            "current_phase": "Concurrency e2e seed",
        }
    ).execute()

    try:
        async with aiohttp.ClientSession() as session:
            status, body = await _post(session, "audit/stop")

        assert 200 <= status < 300, (
            f"audit/stop unexpectedly failed: status={status} body={body[:200]}"
        )

        # No 'running' rows must remain — the handler `.update(status='stopped')
        # .eq('status','running')` zeroes them all.
        running = (
            svc.table("orchestration_jobs")
            .select("id")
            .eq("status", "running")
            .execute()
            .data
            or []
        )
        assert running == [], (
            f"audit/stop left {len(running)} zombie running job(s): "
            f"{[r['id'] for r in running[:5]]}"
        )

        # The seeded job specifically must be marked 'stopped' (not deleted).
        row = (
            svc.table("orchestration_jobs")
            .select("status")
            .eq("id", seeded_job_id)
            .maybe_single()
            .execute()
            .data
        )
        assert row is not None, "Seeded job was deleted, not stopped"
        assert row["status"] == "stopped", (
            f"Seeded job status race-corrupted: {row['status']!r}"
        )

    finally:
        svc.table("orchestration_jobs").delete().eq("id", seeded_job_id).execute()


# ---------------------------------------------------------------------------
# 4) DELETE /leads/clear — destructive, 3/hour. Burst 10 → ≥ 7 rate-limited.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    ENV.get("ALLOW_DESTRUCTIVE_LEADS_CLEAR", "").strip() not in ("1", "true", "yes"),
    reason=(
        "leads/clear burst wipes ALL leads. Set "
        "ALLOW_DESTRUCTIVE_LEADS_CLEAR=1 to opt in — only against a "
        "throw-away DB."
    ),
)
async def test_leads_clear_burst_only_first_three_succeed():
    if not ADMIN_TOKEN:
        pytest.skip("ADMIN_TOKEN required for /leads/clear")

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *[
                _post(
                    session,
                    "leads/clear",
                    method="DELETE",
                    extra_headers={"X-Admin-Token": ADMIN_TOKEN},
                )
                for _ in range(10)
            ]
        )

    statuses = [s for s, _ in results]
    ok, limited, other = _classify(statuses)

    # 3/hour limit. Permit ≤ 3 "successful" responses (200 OR 503 if DB is
    # already empty — both indicate the request landed at the handler).
    assert ok <= 3, (
        f"leads/clear: {ok} succeeded but limit is 3/hour. Statuses: {statuses}"
    )
    assert limited >= 7, (
        f"Expected ≥7 429s on the burst of 10, got {limited}. Statuses: {statuses}"
    )
