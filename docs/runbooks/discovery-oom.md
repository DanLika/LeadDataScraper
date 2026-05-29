# Discovery OOM (Render starter, 512 MB)

**Status**: FURTHER MITIGATED 2026-05-29 (second pass, branch
`fix/discovery-free-plan-survival`). Free-plan-survival mode: smaller batches +
stylesheets blocked + pre-flight 503 + smaller viewport + fail-fast context
timeout. Trade-off: 30→15 max containers per call. `/events` verification on
prod still blocked on [[render-api-key-stale-2026-05-29]] rotation.

## Symptom

Render dashboard → `lead-scraper-backend` (`srv-d89bisbbc2fs73f1pjpg`):

- Service health flips to **unhealthy** ~30–60 s after `/discovery/start` POST.
- `/v1/services/$SVC/events` payload (not `/v1/logs`) shows
  `type=server_failed`, `details.reason.oomKilled.memoryLimit=512Mi`.
- App logs from 25-min window around event contain ZERO error-level entries —
  process killed mid-write, lifespan never re-emits.
- Frontend `/discovery/start` returns 502 / connection reset.
- Backend recovers via Render auto-restart in ~45–90 s; orchestrator job stuck
  in `running` state, swept by `_zombie_orphans_recovery` next pass.

## Root cause

`src/scrapers/discovery_engine.py` launches a fresh Chromium per
`/discovery/start` call (`async with async_playwright() as p:
browser = await p.chromium.launch()`). Asymmetric with
`src/scrapers/enrichment_engine.py` which has a proper shared-pool pattern
with `_get_browser` double-checked lazy launch + `aclose()`.

Single discovery peak ~400–550 MB (Chromium + Google Maps tile/image
download + 10× scroll into virtualised marker list). Two concurrent calls
3 s apart doubled the headroom requirement. 512 MB Render starter died.

The fix backported the enrichment-pool pattern AND added resource-block route
guards (block images/fonts/media/maps/streetview/gstatic), but a single fresh
discovery still peaks above 512 MB even with all three mitigations (verified
reproduce 2026-05-29 09:23 UTC, first discovery against Mostar, 32 s in).

## Fix recipe

PR #397 (commit `ca9d9b06`, merged 2026-05-29) — INSUFFICIENT on starter plan.

**Free-plan-survival pass** (branch `fix/discovery-free-plan-survival`,
2026-05-29) layered on top:

1. `DISCOVERY_MAX_SCROLL_ITERS` default 5 → 3 (`discovery_engine.py:55`).
2. `DISCOVERY_MAX_CONTAINERS` default 30 → 15 (`discovery_engine.py:56`).
3. `_BLOCKED_RESOURCE_TYPES` adds `stylesheet` (we scrape role+aria
   selectors, never paint). Smoke-verified locally 2026-05-29: query
   `"dentist in Mostar"` → 64 result containers in 4.2 s.
4. Chromium launch args: `--disable-gpu --disable-dev-shm-usage
   --disable-extensions`. Intentionally **NOT** `--single-process` — renderer
   crash takes the whole browser, ~50 MB savings not worth it (advisor).
5. Viewport 800 × 600 (smaller framebuffer + fewer compositor tiles).
6. `context.set_default_timeout(15000)` — fail-fast on hung op.
7. **Pre-flight 503 in `task_orchestrator.run_discovery_job`**: when
   `psutil.virtual_memory().available / MB < DISCOVERY_MIN_FREE_MB` (default
   300), raise `HTTPException(503, "Server busy, retry in 30s")` BEFORE the
   `orchestration_jobs` insert and BEFORE Chromium launch. Returns to client
   intact; no orphan job row. Override via env (`0` disables) when on a
   bumped plan.

**Caveat: serialisation was already in place.** `task_orchestrator._discovery_sem
= Semaphore(1)` (line 63) prevents parallel Chromium since PR #397. The OOM
is footprint-per-call, not concurrency. The semaphore is necessary but not
sufficient — that's why the second pass shrinks the footprint instead of
the concurrency.

**Trade-off**: hard cap of 15 containers per `/discovery/start` call. Operators
running larger batches need to either (a) fire multiple sequential discoveries
(semaphore serialises them anyway), or (b) bump the plan and raise both
env knobs.

Until operator picks a path:

```bash
# Confirm OOM is the active cause (NOT just timeout):
RENDER_API_KEY=...  # pull from local .env or 1Password
curl -sS -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/services/srv-d89bisbbc2fs73f1pjpg/events" \
  | jq '.[].event | select(.type == "server_failed") | {ts: .timestamp, reason: .details.reason}'
```

If `oomKilled.memoryLimit=512Mi`: do NOT retry `/discovery/start` until one of
the four follow-ups below ships. Each fresh discovery will OOM again.

**Operator decisions (one needed)**:

1. **Subprocess isolation** — fork Chromium into separate `multiprocessing.Process`
   with own memory accounting; main FastAPI worker survives kernel OOM-kill of
   child. Cleanest single-instance fix.
2. **Bump Render plan** starter → standard (2 GB) ≈ $25/mo. Cost-conservative
   per [ADR-007](../adr/007-render-not-vercel.md) — operator preference.
3. **Drop discovery route** entirely; rely on CSV ingest for lead seeding.
4. **External browser pool** (Browserless.io / Playwright Cloud) ≈ $60/mo.
   Removes Chromium memory from Render instance.

## Recurrence guard

- **CI gate** — `tests/test_discovery_resource_budget.py` (PR #397) asserts
  the semaphore + resource-block + container/scroll caps stay wired. Falls
  green even when peak memory remains over 512 MB — that's an integration
  concern not a unit test. **Free-plan-survival pass note**: the test
  asserts the *3/15 defaults* and the stylesheet entry in
  `_BLOCKED_RESOURCE_TYPES`; revert-by-mistake bumps trip CI.
- **Live verification (operator)**: re-run a single `/discovery/start`
  against prod, then poll `/v1/services/$SVC/events` for `oomKilled`. This
  is the only way to confirm the second pass holds — the CI gate sees
  defaults, not RSS. Blocked on `RENDER_API_KEY` rotation per
  `render_api_key_stale_2026-05-29.md`. **Local-only signal so far**: smoke
  shows containers populated + parent RSS ≤ 44 MB; child Chromium RSS not
  measured (separate process).
- **Diagnostic discipline**:
  - Pull BOTH `/v1/services/$SVC/events` AND `/v1/logs` during an incident —
    OOMs only land in events.
  - `/v1/logs` requires `ownerId` (`tea-d89bdph9rddc7394se1g` for this
    project) — pasted `/v1/services/$SVC/logs?...` returns 400.
  - Cross-check operator-reported UTC times against `events` payload. Operator
    `Z` suffix on a query string ≠ wall-clock UTC. Sarajevo CEST = UTC+2.
- **Don't admin-merge through**: PR #397's CI was green. Per
  [`ci-six-clusters-2026-05-28.md` caveat](./README.md#ci-cluster-discipline),
  "noise" admin-merges have absorbed real regressions before. Verify with
  a live `/discovery/start` smoke after any future change to
  `discovery_engine.py` or `task_orchestrator.run_discovery_job`.

## Related

- Memory: `bug_discovery_oom_2026-05-28.md`, `pr397_oom_fix_INSUFFICIENT.md`
- Code: `src/scrapers/discovery_engine.py:34-100`,
  `src/scrapers/enrichment_engine.py:70-106` (reference pattern),
  `src/core/task_orchestrator.py:run_discovery_job`
- ADR: [007-render-not-vercel.md](../adr/007-render-not-vercel.md)
