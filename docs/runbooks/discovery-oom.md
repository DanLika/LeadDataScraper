# Discovery OOM (Render starter, 512 MB)

**Status**: RESOLVED 2026-05-29 via Render plan bump starter→standard
(2 GB RAM, ~$25/mo). 2 concurrent `/discovery/start` calls verified post-bump:
16 leads, 0 `oomKilled` events. Defense-in-depth code mitigations from this
PR (preflight 503, stylesheet block, Chromium flags, smaller viewport)
retained — they cost nothing on the bumped plan and harden against future
memory spikes from siblings endpoints. Yield caps (scroll iters, container
cap) restored to pre-incident defaults (5 / 30) after the bump since the
2 GB ceiling no longer requires them. See [[render-bump-oom-resolved-2026-05-29]].

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

**Plan bump** (chosen 2026-05-29, ~15:07 UTC): starter → standard
(`plan-srv-006 → plan-srv-008`, ~$25/mo). 2 concurrent `/discovery/start`
calls post-bump returned 16 leads, 0 `oomKilled` events. Reversible via
PATCH back. See [[render-bump-oom-resolved-2026-05-29]] for the operational
recipe and rollback steps.

**Defense-in-depth code mitigations** (PR #412, branch
`fix/discovery-free-plan-survival`) layered on top of #397 and retained
post-bump because cost is nil:

1. `_BLOCKED_RESOURCE_TYPES` adds `stylesheet` (we scrape role+aria
   selectors, never paint). Smoke-verified locally 2026-05-29: query
   `"dentist in Mostar"` → 64 result containers in 4.2 s.
2. Chromium launch args: `--disable-gpu --disable-dev-shm-usage
   --disable-extensions`. Intentionally **NOT** `--single-process` — renderer
   crash takes the whole browser, ~50 MB savings not worth it (advisor).
3. Viewport 800 × 600 (smaller framebuffer + fewer compositor tiles).
4. `context.set_default_timeout(15000)` — fail-fast on hung op.
5. **Pre-flight 503 in `task_orchestrator.run_discovery_job`**: when
   `psutil.virtual_memory().available / MB < DISCOVERY_MIN_FREE_MB` (default
   300), raise `HTTPException(503, "Server busy, retry in 30s")` BEFORE the
   `orchestration_jobs` insert and BEFORE Chromium launch. Returns to client
   intact; no orphan job row. Disable via `DISCOVERY_MIN_FREE_MB=0`.

**Reverted at PR-412 review (post-bump)**: yield caps. Scroll iters and
container cap defaults were temporarily cut to 3 / 15 to survive 512 MB.
With the 2 GB ceiling, those cuts only starved yield. Restored to 5 / 30.
Bumped-back-down knobs are still env-tunable for tighter plans.

**Caveat: serialisation was already in place.** `task_orchestrator._discovery_sem
= Semaphore(1)` (line 63) prevents parallel Chromium since PR #397. The OOM
was footprint-per-call, not concurrency. Semaphore retained as part of the
defense-in-depth posture.

```bash
# Confirm OOM is the active cause (NOT just timeout):
RENDER_API_KEY=...  # pull from local .env or 1Password
curl -sS -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/services/srv-d89bisbbc2fs73f1pjpg/events" \
  | jq '.[].event | select(.type == "server_failed") | {ts: .timestamp, reason: .details.reason}'
```

If `oomKilled` reappears post-bump: the standard plan has been outgrown.
Escalation options (none currently chosen, listed for future regress):

1. **Subprocess isolation** — fork Chromium into separate `multiprocessing.Process`
   with own memory accounting; main FastAPI worker survives kernel OOM-kill of
   child. Cleanest single-instance fix.
2. **Bump Render plan** standard → pro (4 GB) ≈ $85/mo.
3. **External browser pool** (Browserless.io / Playwright Cloud) ≈ $60/mo.
   Removes Chromium memory from Render instance.

## Recurrence guard

- **CI gate** — `tests/unit/test_discovery_oom_mitigation.py` (PR #397)
  asserts the semaphore + resource-block + container/scroll caps stay
  wired. Falls green even when peak memory remains over the plan ceiling —
  that's an integration concern not a unit test. Asserts post-bump
  defaults (5 / 30) AND the stylesheet kill-list case (URL alone doesn't
  match `_BLOCKED_URL_PATTERN`, so the abort is pinned to the
  `_BLOCKED_RESOURCE_TYPES` frozenset entry). Accidental kill-list removal
  trips CI.
- **Post-bump live verification (operator)**: re-run 2 concurrent
  `/discovery/start` calls against prod, then poll
  `/v1/services/$SVC/events` for `oomKilled`. Done 2026-05-29 — 0 events.
  Repeat after any future plan change or `discovery_engine.py` /
  `task_orchestrator.run_discovery_job` change.
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
