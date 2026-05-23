# Phase 15 — Live verification 2026-05-23

**Branch:** `main` → `chore/phase15-findings-2026-05-23`
**Method:** chrome-devtools-mcp against `npm run build && npm run start` prod
build (local) + Render prod.
**Auth:** `test-lds4@example.com` (single-operator, throwaway Supabase Auth user).
**Scope:** Verify every shipped feature in a real browser; log findings, do
NOT fix bugs.

> Re-run via the same chrome-devtools-mcp setup if the auth / proxy /
> orchestrator wiring changes. Each numbered section is a 2026-05-23
> point-in-time snapshot.

---

## Servers

| Layer | Local | Prod (Render) |
| --- | --- | --- |
| Backend | `http://127.0.0.1:8000` (uvicorn `backend.main:app`, `.venv/bin/python`) | `https://lead-scraper-backend.onrender.com` — **unreachable** |
| Frontend | `http://localhost:3100` (`npm run build && npm run start`) | `https://lead-scraper-frontend.onrender.com` — **unreachable** |

Local frontend started with inline env (`.env.local` lacks both):
`ALLOWED_ORIGINS=http://localhost:3100 BACKEND_URL=http://127.0.0.1:8000`.

---

## US-leads seed (15.1)

20 rows inserted via supabase MCP `execute_sql` with `lead_source = '_us_test_'`.
Kept after the run for the operator's actual outreach. Final DB state:

| lead_source | count |
| --- | --- |
| `_us_test_` | 20 |
| `(null)` (pre-existing) | 1 |

Perf-test rows (`lead_source='_perf_test_'`, 500 rows) cleaned up in 15.18.

---

## Status table — local prod

| Step | Test | Status | One-line evidence |
| --- | --- | --- | --- |
| 15.2 | Cold-load smoke (login) | ✅ pass | 0 console errors, 1 doc request, form fields present, no `/api/proxy/*` before user touch |
| 15.3 | Login flow + cookies | ✅ pass | Login 303→`/`; `document.cookie` has only `__next_hmr_refresh_hash__` — sb-* cookies HttpOnly |
| 15.4 | CSP nonce regression | ✅ pass | 15→17 nonced scripts, 0 unnonced inline; nonces rotate per reload (`5x6x…` ≠ `5FKg…`); `<body data-nonce="1">` is diagnostic flag (`layout.tsx:32`) |
| 15.5 | OfflineBanner + WebVitalsReporter | ⚠ partial | OfflineBanner mounts on `offline` event, unmounts on `online` ✓. Vitals fire on visibility-change ✓. But pre-login vitals 307→`/login` (Sev-2) — see findings #4 |
| 15.6 | Virtualized LeadTable (500+) | ✅ pass | 521 in DB, 350 loaded after 22 "Load more" clicks; virtualizer holds 27 DOM rows; CLS=0.00, no long-task insights flagged across 5 s programmatic scroll |
| 15.7 | Filter + sort + URL sync | ⚠ partial | `?status=`, `?q=`, `?sort=` all sync ✓. Reload preserves ✓. **`Clear filters` clears internal state but does NOT strip URL** (Sev-2) — see finding #2 |
| 15.8 | Multi-tab signout cascade | ✅ pass _(P0a retracted Phase 16)_ | Tab B authed via shared cookie ✓. Original 15.8 reported Sign Out as P0; **Phase 16 re-test on a fresh `npm run build` could not reproduce** — Sign Out works in 4/4 scenarios (`/`, `/` post-chat + dismissed plan, `/insights`, `/campaigns`). See Phase 16 update below for root cause |
| 15.9 | AIChat plan card | ✅ pass | `"how many leads do I have"` → STATUS_CHECK auto-exec → `"521 leads total — 521 Pending."`. `"find 3 coffee shops in Austin Texas"` → plan card with Task=DISCOVERY_SEARCH, Params, Confirm & Execute + Dismiss buttons. Dismissed to avoid Playwright burn |
| 15.10 | Drag-drop CSV | ⏭ skipped | No `data-testid="drop-overlay"` in current DOM; could not locate drop target via `[class*="drop"]` / `[aria-label*="drop"]`. Drop dispatched on `<body>` produced no upload — either selector contract drifted or feature not landed |
| 15.11 | Faza 9 regression sweep | ✅ partial | 9.4 P3 a11y **resolved** (0 form fields without id/name/aria-label across `/`). 9.4 P2 search **resolved** (rapid type of `"pacific"` over 210 ms → `proxy_leads_delta = 0` — search is client-side filter). 9.5 scroll **resolved** at 350 rows. **9.4 P2 orchestrator/active polling STILL PRESENT** (18 calls in 30 s session — finding #7). **9.7 Inter font fallback STILL PRESENT** (`document.fonts.size === 0`). 9.4 P1 fetch-signal + 9.9 login spinner NOT exercised this run |
| 15.12 | Browser security headers | ✅ pass | Full CSP w/ nonce + strict-dynamic on every response (doc + `/api/proxy/*`). HSTS `max-age=63072000; includeSubDomains; preload`. XFO DENY, X-CTO nosniff, Referrer-Policy, Permissions-Policy all present. Cache-Control `private,no-store,max-age=0` on authed HTML, `no-store` on `/api/proxy/*`. `X-Request-ID` propagated. Inline-script via `innerHTML +=` blocked by HTML parser ✓. **Missing (BookBed Phase D backport candidates):** COOP, CORP, COEP, X-Permitted-Cross-Domain-Policies — finding #11 |

## Status table — prod (Render)

| Step | Test | Status | Evidence |
| --- | --- | --- | --- |
| 15.13 | Prod URL + smoke | ❌ HARD STOP | `https://lead-scraper-frontend.onrender.com/` → `HTTP/2 404` `x-render-routing: no-server`, `cf-ray: a002cb28d976ec2a-ZAG`. `https://lead-scraper-backend.onrender.com/` → connect/curl timeout at 25 s. Both default hostnames docs ([docs/runbooks/operator-guide.md], [docs/legal/privacy-policy.md]) refer to. Service is paused / deleted / never deployed — not reachable from this session |
| 15.14 | HSTS + secure cookies | ⏭ skipped | Hard stop on 15.13 |
| 15.15 | Prod network waterfall | ⏭ skipped | Hard stop on 15.13 |
| 15.16 | Sentry events delivered | ⏭ skipped | Hard stop on 15.13 |
| 15.17 | Web Vitals beacons on prod | ⏭ skipped | Hard stop on 15.13 |

> **Surface to operator:** the production Render service is not currently
> serving traffic at the documented hostnames. Either the services were
> suspended (free plan inactivity / billing), removed, or the docs URLs
> are stale. This is the first thing to fix before another Phase-15
> sweep can complete the prod tier.

---

## Findings table

Severity legend: **P0** = blocks ship; **P1** = ship-blocker if user-facing,
fix in this sprint; **P2** = real bug, plan into next 1–2 PRs; **P3** =
polish.

| # | Sev | Title | Where | Repro | Recommended fix |
| --- | --- | --- | --- | --- | --- |
| 1 | ~~P0~~ **RETRACTED Phase 16** | ~~Sign-out button click does nothing~~ | `Sidebar.tsx:211-226` — source unchanged since 2026-05-15 | **Could not reproduce on fresh `npm run build`.** 4/4 sign-out scenarios green: `/` clean, `/` after AIChat + dismissed plan card, `/insights`, `/campaigns`. Each fires `[SIGNOUT] click handler entered` / `fetch returned 200` / `finally: router.replace(/login)` (verified by temporary `console.log` instrumentation, removed before commit) and URL redirects to `/login`. **Root cause of original false positive:** Phase 15 setup ran `pkill -f "next start" -f "uvicorn backend"` (single command). On macOS, `pkill -f` honors only the LAST `-f` pattern — so the previous `next-server` (PID 59710 from 2026-05-22 18:53) was never killed and kept serving cached pre-build output. Sidebar handler was correct all along | None required. Lesson: when restarting servers, run separate `pkill` calls per pattern and verify with `pgrep` before claiming a fresh build is under test |
| 2 | P2 | "Clear filters" clears state but does not strip URL params | `frontend/app/components/FilterBar.tsx` (or wherever Clear lives) | URL: `?status=Pending&q=pacific&sort=seo_score_desc` → click Clear filters → URL unchanged; reloading re-applies the stale filters | After internal state reset, call `router.replace('/')` (or similar) so URL becomes the source of truth on next reload |
| 3 | P2 | `TOTAL LEADS` stat card shows page-loaded count, not DB total | Dashboard stats (`HealthChart` / `StatsCards`) | DB has 521 leads; AIChat STATUS_CHECK answers `"521 leads total"`; stat card shows `50` (initial page size). After "Load more"×22 the card creeps up to `350` matching loaded-rows. Insights endpoint hallucinated "180 records" the same render | Either (a) hit `/stats` (cached, src/utils/stats_cache.py) for the total, or (b) rename the card to "LOADED" so operators understand. The current text is misleading |
| 4 | P2 | Pre-login WebVitalsReporter beacons 307→`/login`; vital data is lost AND auth gate churns | `frontend/app/components/WebVitalsReporter.tsx` (mounted in `app/layout.tsx`) | Cold-load `/login` → DevTools Network shows `POST /api/proxy/metrics` 307 → `POST /login?next=%2Fapi%2Fproxy%2Fmetrics` 200 (×3) | Either (a) don't mount `<WebVitalsReporter>` on `/login`, or (b) add `/api/proxy/metrics` to the public-path allowlist in `frontend/utils/supabase/middleware.ts` (rate-limit already exists at backend) |
| 5 | P2 | WebVitalsReporter only flushes on visibility-change / pagehide — no eager LCP/INP send | same | Reload `/` and idle 20 s → no `POST /api/proxy/metrics`. Dispatch `visibilitychange` (hidden) + `pagehide` → 2× POST 200 lands | Default web-vitals behavior; document explicitly. If you want eager flush, call `onCLS` / `onLCP` / `onINP` with `{reportAllChanges: true}` — at the cost of extra beacons per page-view |
| 6 | P2 | AI Insights endpoint hallucinates lead counts | `backend/main.py::_get_strategic_insights` / `src/core/agentic_router.py` insights prompt | Trigger /insights via sidebar widget with 521 leads in DB → Gemini reply asserts `"100% of the 180 records pending technical audits"`. Actual count was 521 | Either pin `total_count` into the prompt as a fenced fact, or post-process the response and reject if a number appears that's not in the source data (CLAUDE.md mentions `test_insights_quality.py::no-invented-numbers`; production guard would be similar shape) |
| 7 | P2 | `/api/proxy/orchestrator/active` polling storm (Faza 9.4 P2 not fixed) | Background poller in `frontend/app/page.tsx` or hook | Logged-in idle dashboard → 18 GET `/api/proxy/orchestrator/active` in ~30 s; no `visibilityState !== 'visible'` pause, no exponential backoff while no job is running | Pause poller when `document.hidden`; back off (e.g., 2 s → 10 s) when no active job for N intervals |
| 8 | P3 | `ForcedReflow` insight raised on 50 s reload trace | reload trace `tests/perf/phase15-reload-trace.json.json.gz` | Trace summary lists `ForcedReflow` with bounds spanning 34 s of the trace — coincides with orchestrator-poller re-render window | Run `npx chrome-trace-analyzer` or inspect the call tree for `Layout` after `Recalculate Style` clusters; typical culprit is a `el.offsetHeight` inside a write loop |
| 9 | P3 | `/api/proxy/leads?limit=50` refetched 3× in 30 s idle | Same poller cascade | See network log in this report — leads refetch fires alongside orchestrator polls | Same fix as #7 — visibility-pause + dedupe in-flight |
| 10 | P3 | Inter font silent fallback persists (Faza 9.7) | `frontend/app/globals.css` declares `--font-main: 'Inter'` but no `.woff*` shipped | `document.fonts.size === 0`; computed `body` font = `Inter, system-ui, -apple-system, sans-serif` falls through to `system-ui` | Either drop `'Inter'` from the stack OR wire `next/font/google` with `display: 'swap'` |
| 11 | P3 | Missing browser headers vs BookBed-Website (Phase D backport) | `frontend/next.config.ts` static headers + `frontend/proxy.ts` per-request CSP | Inspect `Response Headers` on `/` — present: CSP, HSTS, XFO, X-CTO, Referrer-Policy, Permissions-Policy. **Absent:** `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy`, `Cross-Origin-Embedder-Policy`, `X-Permitted-Cross-Domain-Policies` | Backport per CLAUDE.md "Cross-repo strategy" Phase D — these are no-cost wins (header stamps), 30 min |
| 12 | P3 | Drag-drop drop target has no stable selector | unknown — `data-testid="drop-overlay"` not present | Construct `DataTransfer` + `File('phase15.csv')`, dispatch `dragenter`/`dragover`/`drop` on `<body>` → no upload fired | Add `data-testid="drop-overlay"` (or similar) to the drop overlay so this contract test can run in MCP and `tests/e2e/drag-drop.spec.ts` |
| 13 | **P0 ops** | Prod (Render) unreachable + ALL recent CI on `main` failing | Both Render services + GitHub Actions runs `26328800835` / `26327154866` / `26327154878` / `26327154888` | `curl https://lead-scraper-frontend.onrender.com/` → HTTP 404 `x-render-routing: no-server`. Backend connect-timeouts at 25 s. **Phase 16 follow-up:** `gh run list --branch main --limit 8` shows EVERY workflow run since `2026-05-23 07:39 UTC` failed — every Security job (schema-drift, RLS, query-plans, JSONB, gitleaks, semgrep, pip-audit, npm-audit, …), Quality Ratchet, every main-matrix pytest (3.11/3.12/3.13), every npm test (node 20/22). Breadth + simultaneity suggests env-level failure (expired/missing CI secret, runner config, or a single broken `pip install`) rather than a code bug. Prod outage is likely a downstream consequence of the gated deploy chain not running | **Operator action — not a code fix.** (1) Open one failed run in the Actions UI and read step logs (`gh run view <id> --log-failed` only surfaces step names when failure is at job setup level). (2) Most likely culprit per CLAUDE.md is `SUPABASE_DATABASE_URL` (fail-closed in schema-drift) or a Sentry / Render API secret expiring. (3) Restore CI green, then investigate Render — `plan: starter` doesn't auto-suspend, so 404 `no-server` means the service was paused / re-provisioned / deleted manually |

---

## Status snapshot — 2026-05-23 session

Six surgical-fix PRs shipped after the initial findings. All branch off `origin/main` and merge independently.

| # | Sev | Status | PR |
| --- | --- | --- | --- |
| 1 | ~~P0~~ | **RETRACTED** Phase 16 (false positive — stale build from Phase 15's broken `pkill -f`) | this PR (#227) |
| 2 | P2 | **PR open** — `router.replace('/')` in `clearFilters` to bypass the read-effect race | [#235](https://github.com/DanLika/LeadDataScraper/pull/235) |
| 3 | P2 | **PR open** — rename `TOTAL LEADS` → `LOADED` (honest cursor-pagination semantics) | [#241](https://github.com/DanLika/LeadDataScraper/pull/241) |
| 4 | P2 | **PR open** — `/api/proxy/metrics` exact-match in public-path allowlist | [#234](https://github.com/DanLika/LeadDataScraper/pull/234) |
| 5 | P2 | **No fix** — default `web-vitals` behaviour. Opt into `{reportAllChanges:true}` later if eager flush worth the extra beacons per page-view | — |
| 6 | P2 | **No fix yet** — needs Gemini test fixtures (`test_insights_quality.py::no-invented-numbers`) to validate a `total_count` prompt-pin without regressing other insights. Out of scope for a surgical PR | — |
| 7 | P2 | **PR open** — `document.hidden` guard + `visibilitychange` re-fire on the 5 s cross-tab poller | [#233](https://github.com/DanLika/LeadDataScraper/pull/233) |
| 8 | P3 | **Re-trace after #233 merges** — forced reflow was inside the polling re-render window; visibility-pause likely halves the affected duration | — |
| 9 | P3 | **Covered by #233** — leads refetch fires alongside orchestrator polls; same visibility cascade reduces churn | (via #233) |
| 10 | P3 | **PR open** — drop `'Inter'` from `--font-main` (literal never loaded) | [#239](https://github.com/DanLika/LeadDataScraper/pull/239) |
| 11 | P3 | **PR open** — COOP / CORP / X-Permitted-Cross-Domain-Policies header stamps | [#237](https://github.com/DanLika/LeadDataScraper/pull/237) |
| 12 | P3 | **RETRACTED** — `data-testid="drop-overlay"` IS present at `page.tsx:1024`; only renders while `isDragging===true`. A proper drag-drop MCP test must dispatch `dragenter` on `[data-testid="dashboard-root"]` first to flip `isDragging`, then `drop` on the now-rendered overlay | — |
| 13 | **P0 ops** | **Operator action** — restore failing CI secret then confirm Render service state. No code change possible | — |

**Outstanding for the operator:**
1. Restore CI green on `main` — every run since 2026-05-23 07:39 UTC has failed (most-likely a single missing/expired secret per #13).
2. Confirm Render prod state once CI is green. `plan: starter` shouldn't auto-suspend; 404 `no-server` implies manual pause / re-provision / billing.
3. Re-run Phase 15 prod tier (`15.13`–`15.17`) once both restored.


## Verified-working surface (positives worth documenting)

- **CSP nonce + `strict-dynamic`**: hydration works under prod CSP; 17 scripts carry a fresh nonce per reload; `__next_f.push` bootstrap blocks all nonced; inline `<script>` via `innerHTML +=` blocked by HTML parser. Sev-1 regression fix from `d3a90ff` holds.
- **Origin gate**: state-changing POST (`/api/proxy/*`) require matching `Origin`. With `ALLOWED_ORIGINS=http://localhost:3100`, dashboard writes succeed.
- **HttpOnly session cookies**: `document.cookie` only exposes `__next_hmr_refresh_hash__`. Supabase `sb-*` cookies are not JS-visible.
- **`/api/proxy/leads` cursor pagination**: returns `{leads, next_cursor, has_more}`; "Load more" repeatedly advances.
- **AIChat STATUS_CHECK auto-exec** lands accurate count (`"521 leads total — 521 Pending."`).
- **AIChat plan-card** renders Task/Params/Confirm/Dismiss for DISCOVERY_SEARCH; Dismiss tears down without execution.
- **Filter/sort/search URL sync** in dashboard (URL is source of truth on reload; query-param vocabulary `status` / `q` / `sort` consistent with `docs/e2e-and-frontend-contracts.md`).
- **TanStack-virtual** holds DOM at 27 row nodes throughout 5-second programmatic scroll over 350 loaded rows. CLS 0.00.
- **OfflineBanner**: appears on `offline` event ("Offline — 0 actions queued. Will retry when reconnected."), disappears on `online`.
- **WebVitalsReporter** beacons POST `/api/proxy/metrics` 200 on `visibilitychange`/`pagehide` (post-login).
- **All inputs have `aria-label` or id/name**: 0 unlabelled form fields across the dashboard (Faza 9.4 P3 resolved).
- **Search rapid-type** does not fire `/api/proxy/leads` — client-side filter on already-loaded rows (Faza 9.4 P2 search-debounce concern moot).
- **Security headers**: HSTS, XFO DENY, X-CTO nosniff, Referrer-Policy, Permissions-Policy, full per-request CSP. `X-Request-ID` propagated end-to-end.

---

## Cross-reference with Faza 9 (`tests/perf/console-sweep.md`)

| Faza 9 finding | Faza 15 status | Note |
| --- | --- | --- |
| 9.4 P1 — `fetch({signal})` non-AbortSignal on insights refresh | _not exercised_ | Did not click Refresh AI Insights ×5 this run to avoid burning Gemini budget for a regression check |
| 9.4 P2 — orchestrator/active poller no visibility-pause | **still broken** (finding #7) | 18 calls / 30 s, no backoff |
| 9.4 P2 — search input no debounce | **resolved (moot)** | Search now client-side filter — no network fires on type |
| 9.4 P3 — form-field id/name missing on `/` + `/campaigns` | **resolved** | 0 inputs without id/name/aria-label on `/` |
| 9.5 — scroll-CPU with 500 rows | **resolved** | Virtualizer holds 27 DOM rows; CLS=0; no long-task insights flagged |
| 9.7 — Inter font silent fallback | **still present** (finding #10) | `document.fonts.size === 0` |
| 9.9 — login no spinner / no throttle toast | _not exercised this run_ | Login was a single 200; throttle not triggered |

---

## Phase 16 update (2026-05-23, P0 follow-up)

Spec asked: fix 2 P0s, surface root causes. Outcome:

- **P0a Sign Out — RETRACTED.** False positive caused by Phase 15's
  `pkill -f "next start" -f "uvicorn backend"` only honoring the last
  `-f` on macOS — so the stale `next-server` (PID 59710 from
  2026-05-22 18:53) kept serving cached pre-build output. After a
  clean rebuild (with separate `pkill` calls and `pgrep` verification),
  Sign Out works in 4/4 scenarios. Source `Sidebar.tsx:211-226` was
  never broken.

- **P0b Render prod — investigated, surfaced as operator action.**
  `curl https://lead-scraper-{frontend,backend}.onrender.com/` still
  returns 404 `no-server` / 25 s timeout after 5 wake attempts spaced
  15 s apart. New signal from Phase 16: **every CI run on `main` since
  2026-05-23 07:39 UTC has failed** (Security, Quality Ratchet, every
  pytest matrix, every npm test). Breadth + simultaneity → env-level
  failure (most-likely culprits: an expired/missing `SUPABASE_DATABASE_URL`,
  Sentry, or Render API secret, or one broken `pip install`). Operator
  must read the actual job logs in the Actions UI and restore the
  secret / lockfile. Prod outage is likely downstream of the gated
  deploy chain not running.

## Recommendation — next fix-sprint priority (updated post-Phase-16)

In order of operator-visible impact:

1. **Restore CI green on `main` (#13 root cause)** — operator action.
   Without this, no Render deploys can happen and no merges land safely.
2. **Resolve prod Render unreachability (#13 consequence)** — once CI
   is green, redeploy and verify. Until this is back, no Phase-15 prod
   tier can be re-verified.
3. **Stop the orchestrator-active polling storm (#7)** — 1-line
   `document.hidden` guard + `setInterval` exponential backoff. Saves
   continuous backend load and (in prod) Render compute minutes.
4. **Either fix `Clear filters` URL strip (#2) OR rename the stat card
   (#3)** — both surface "stale state, fresh paint" confusion to the
   operator. Easy wins.
5. **Pre-login vitals 307 (#4)** — one-line allowlist edit OR
   conditional `<WebVitalsReporter>` mount on auth pages.
6. **AI Insights hallucinates totals (#6)** — pin `total_count` into
   the prompt as a fenced fact, or post-validate response numbers
   against the data array.
7. **Polish set:** Inter font (#10), backport COOP/CORP/COEP (#11),
   drag-drop selector (#12), `ForcedReflow` follow-up (#8).

---

## Artifacts left on disk

- `tests/perf/phase15-findings.md` — this file.
- `tests/perf/phase15-reload-trace.json.json.gz` — Chrome trace of cold reload (LCP 707 ms, CLS 0.00, ForcedReflow flagged).
- `tests/perf/phase15-scroll-trace.json.json.gz` — Chrome trace of programmatic 5 s scroll over 350 loaded rows (CLS 0.00, 27 DOM rows).

Servers shut down at end of run. 20 `_us_test_` leads kept in Supabase
for the operator's actual outreach. 500 `_perf_test_` rows purged.
