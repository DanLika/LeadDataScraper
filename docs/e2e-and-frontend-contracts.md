# E2E test suite + frontend contracts

This doc covers the contracts and tooling added in the recent
test-suite-build session. It's a companion to `CLAUDE.md` — fold any
section in there at your pace; the goal here is to keep the new
surface searchable without bloating the main doc.

## Filter ↔ URL state sync (`frontend/app/page.tsx`)

Dashboard filter state is mirrored in the URL. Vocabulary:

| Query param | Backing state | Notes |
|---|---|---|
| `?segment=<value>` | `filterSegment` | `'all'` → not in URL |
| `?status=<Completed\|Pending\|Failed>` | `filterAuditStatus` | `'all'` → not in URL |
| `?min=<0..100>` | `filterMinScore` | filters outreach_score, not seo_score |
| `?q=<term>` | `searchTerm` | replaces legacy `?search=` |
| `?sort=<key>` | `sortKey` | see `SortKey` below |

`SortKey` (in `frontend/app/components/FilterBar.tsx`):
```
created_at_desc   ← DEFAULT_SORT
seo_score_desc
seo_score_asc
outreach_score_desc
name_asc
name_desc
```

Implementation: bidirectional via two effects in `app/page.tsx`, guarded
by `filterReadInFlightRef` + `queueMicrotask` reset so reads don't
trigger immediate writes (would loop). Reads on every `searchParams`
change → back/forward rehydrates. Writes on state change with a diff
guard so default state doesn't push. `router.push` (not replace) so each
user-driven change is a real history entry.

Legacy `?search=<term>` from Insights/Campaigns/Sidebar still works — the
cross-page-nav bridge effect translates it to `?q=<term>` on consume so
both effects share one vocabulary.

Lead `<tr>` rows expose `data-segment`, `data-seo-score`,
`data-unique-key` for E2E ground-truth reads. Never assume the
underlying state matches the rendered table — read the attrs.

Pinned by `frontend/e2e/filter-sort.spec.ts`.

## API client contract (`frontend/utils/apiConfig.ts`)

`apiFetch()` centralises three concerns the rest of the app skips:

1. **401 from `/api/proxy/*`** → `window.location.href =
   /login?next=<path>` + throws `Session expired`. Fires when Supabase
   revalidates and rejects (expired/revoked). Loop-guarded so calls
   from `/login` itself don't bounce.
2. **Redirected to `/login`** (middleware HTML redirect echoed into a
   fetch) → same bounce path; guards `.json()` callers from parsing
   login HTML.
3. **`navigator.onLine === false`** on POST/PUT/PATCH/DELETE → enqueues
   into `frontend/utils/offlineQueue.ts` and throws
   `Offline — request queued for retry`. GETs throw without queueing.
   Queue auto-drains on the `online` event.

`OfflineBanner` (`frontend/app/components/OfflineBanner.tsx`) mounts at
the root in `app/layout.tsx`. Sticky top banner with queued count;
switches to "reconnected — retrying…" during drain.
`data-testid="offline-banner"`. Queue is **in-memory only** — a reload
drops it. Documented; acceptable for the single-operator deploy. If
multi-instance ever lands, swap to `sessionStorage` / `IndexedDB`.

Pinned by `frontend/e2e/network-resilience.spec.ts`.

## Cross-tab orchestration visibility (`GET /orchestrator/active`)

Backend `backend/main.py` exposes:

```
GET /orchestrator/active  (X-API-Key required, 60/min)
  → { "job": <most recent running|starting orchestration_jobs row> | null }
```

Process-local `ParallelAuditor` state isn't shared across workers; the
authoritative signal is the DB row. Frontend polls this every 5 s while
the tab has no orchestrator job of its own. On pickup, calls
`setOrchestratorJob(data.job)` — existing running-indicator UI lights
up automatically. Pauses once a job is adopted; per-job poller takes
over.

Pinned by `frontend/e2e/multi-tab.spec.ts`.

## Drag-drop CSV ingest (`frontend/app/page.tsx`)

Drop target is the `dashboard-container` root. Rules:

- `isFileDrag()` checks `dataTransfer.types.includes('Files')` — text
  drags don't open the overlay.
- `dragCounter` ref prevents overlay flicker on child-element crossings.
- Overlay: `data-testid="drop-overlay"`, `role="status"`,
  `pointer-events: none` (drop events still reach the underlying
  container).
- Drop semantics:
  - empty → no-op
  - `loading === true` → reject with toast
  - multi-file → take first + toast naming ignored count
  - non-CSV → reject naming actual extension/type

`ingestFile(file: File)` is the shared ingest path — both the
`#csv-upload` change handler and the drop handler call it. Pinned by
`frontend/e2e/csv-drag-drop.spec.ts`.

## E2E test suite (`frontend/e2e/*.spec.ts`)

### Running

```bash
cd frontend
npm run e2e:install                       # browsers once
npm run e2e                               # matrix
npm run e2e -- --project=chromium         # single browser
npm run e2e -- e2e/auth.spec.ts           # single file
npm run e2e:trace                         # open last failed trace
npm run e2e:report                        # open HTML report
```

Failure artifacts (kept via `playwright.config.ts`): trace + video +
screenshot.

### Projects

| Project | Devices | Spec scope |
|---|---|---|
| `chromium` | Desktop Chrome | all except mobile.spec.ts (visual.spec.ts macOS-only via `test.skip(process.platform !== 'darwin')` at spec top) |
| `firefox` | Desktop Firefox | all except full-flow + mobile + visual |
| `webkit` | Desktop Safari | all except full-flow + mobile + visual |
| `iphone-14` | `devices['iPhone 14']` | mobile.spec.ts only |
| `pixel-7` | `devices['Pixel 7']` | mobile.spec.ts only |

### Required env

| Var | Used by |
|---|---|
| `E2E_BASE_URL` | all |
| `E2E_EMAIL`, `E2E_PASSWORD` | all |
| `E2E_SUPABASE_URL`, `E2E_SUPABASE_SERVICE_ROLE_KEY` | DB-touching specs |
| `E2E_BACKEND_URL`, `E2E_API_KEY`, `E2E_ADMIN_TOKEN` | full-flow.spec.ts `/leads/clear` invariant |
| `E2E_PROD=1` | security-headers.spec.ts (gates HSTS) |
| `E2E_PROD_COOKIE_SECURE=1` | auth.spec.ts (gates `Secure` cookie assertion) |

### Specs

| File | What it pins |
|---|---|
| `auth.spec.ts` | Anon→/login, throttle 5/60s, cookie floor (HttpOnly/Lax/Secure), X-API-Key never client, signout, replayed-cookie rejection |
| `security-headers.spec.ts` | CSP `script-src 'self'` no unsafe-*, HSTS 2y + includeSubDomains + preload, XFO=DENY, XCTO=nosniff, Referrer-Policy, no console errors, no mixed content |
| `mobile.spec.ts` | No h-overflow, CTAs in viewport, sidebar→hamburger, modal fit + scroll, inputs ≥16px (iOS no-zoom) |
| `csv-upload.spec.ts` | Canonical 10-row, messy-headers (AI mapper), UTF-8 BOM, 50MB+1 → proxy 413, formula-injection → sanitised in DB |
| `csv-drag-drop.spec.ts` | Single drop ingests, multi-file (first only), non-CSV reject, drop-while-pending reject, drag-leave UI reset |
| `a11y.spec.ts` | `@axe-core/playwright` on 4 routes (critical+serious), allowlist with expiry, keyboard nav reaches all, ESC closes, Enter submits, focus visible |
| `filter-sort.spec.ts` | Segment filter, seo_score desc monotonic, combine, clear, URL share, browser-back restore |
| `modals.spec.ts` | Click-outside, ESC, submit stays open until response, focus trap + return to trigger |
| `aichat.spec.ts` | Plan card render, dismiss no-execute, confirm fires /execute, read-only auto-exec, ambiguous clarification, rate-limit feedback. All mocked `/api/proxy/ask` |
| `network-resilience.spec.ts` | 500→toast, 401→/login, offline→queue→drain, slow-3G skeleton, malformed JSON, mid-abort |
| `multi-tab.spec.ts` | Shared session, cross-tab job visibility, signout 401-redirect, no auto-recovery, concurrent /process-all DB invariant |
| `polling.spec.ts` | Orchestration tick propagation, completion convergence, refocus catches up |
| `exports.spec.ts` | Full + CRM + Campaign CSV: row count vs DB, headers, formula-injection guard, BOM tolerance, no mojibake |
| `locale.spec.ts` | en-US / hr-HR / bs-BA contexts: diacritics, Intl number grouping, date order |
| `navigation.spec.ts` | Scroll restoration on back, campaigns detail→list→detail, reload rehydrates from URL, modal-state-on-refresh contract |
| `full-flow.spec.ts` | Discover→audit→hunt→outreach→campaign→CSV→`/leads/clear` (~20 min, chromium only, real backend) |
| `visual.spec.ts` | 7 `toHaveScreenshot` baselines (`/login`, dashboard empty + populated, `/insights`, `/campaigns`, outreach modal, AI plan card) with `maxDiffPixelRatio: 0.01`; all upstream APIs mocked via `page.route`; baselines at `frontend/e2e/visual.spec.ts-snapshots/` (chromium-darwin only — `test.skip` on non-darwin platforms, `testIgnore` on Firefox/WebKit projects); `.gitignore` exception lets PNGs commit. Regenerate locally on macOS via `npm run e2e -- --update-snapshots e2e/visual.spec.ts`. See the snapshots dir README for capture env + when to regen |
| `memory-soak.spec.ts` | 50-cycle heap delta <50 MB + detached-node delta <500. Chromium-only CDP. ~15 min |

### CI

`.github/workflows/e2e.yml`:
- Chromium-only on PRs touching `frontend/**`
- Full browser matrix on `main` push
- Fork-PR guard (mirrors `security.yml`)
- Failure artifacts: `frontend/test-results/` + `frontend/playwright-report/`, 14-day retention

## Cooperative-cancel Python test (`tests/test_orchestrator_cooperative_cancel.py`)

Live integration. Seeds 50 fixture leads, starts `/orchestrator/start`,
stops at 3 s, drains via `last_processed_at` count stability, asserts
each row is in exactly one valid terminal state — **untouched**
(Pending + all audit outputs NULL), **Completed** (audit_results +
seo_score ∈ [0, 100]), or **Failed** (last_error set). Any other shape
is a torn write.

Drain signal: `processed_count` ticks once per chunk (useless for
1-chunk runs); `last_processed_at` is set on every touched row.

Env: `BACKEND_BASE_URL` (default `http://127.0.0.1:8000`),
`API_SECRET_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`. Skips
silently if any unset. Self-fails if the job completes before stop —
raise `LEAD_COUNT` or use a slower fixture site.

## Ops + monitoring inventory

`.github/workflows/`:

| Workflow | Trigger | Purpose |
|---|---|---|
| `post-deploy-smoke.yml` | Render webhook (`render-deploy`) | 5-check smoke + Render API rollback on smoke-fail-with-deps-healthy |
| `synthetic-monitor.yml` | cron `*/5 * * * *` | 5-min heartbeat, gist-persisted history, Slack on 3rd-strike |
| `e2e.yml` | PR / main push on `frontend/**` | Playwright matrix + artifact upload |
| `preview-smoke.yml` | `render-preview-deploy` `repository_dispatch` | PR-preview URL smoke, comments PR, blocks merge on fail (no rollback) |
| `data-integrity.yml` | cron `17 4 * * *` | Daily silent integrity assertions on prod data |

`.github/scripts/`:

| Script | Purpose |
|---|---|
| `post-deploy-smoke.mjs` | 5 checks: liveness, schema drift, `/ask`, frontend `/login`, CSP |
| `dependency-health.mjs` | Gemini, Supabase REST + service_role, Maps, Render API — runs BEFORE code smoke |
| `synthetic-monitor.mjs` | 4 lightweight checks + history rotation in a private gist |
| `schema-migration-smoke.mjs` | Column diff via PostgREST OpenAPI, RLS-denies-anon, `add_lead_column` pin, `exec_sql` absence guard |
| `auth-smoke.mjs` | Playwright cookie-floor smoke in prod |
| `contract-smoke.mjs` | AJV-driven JSON Schema validation for endpoint contracts (`tests/contracts/*.json`) |
| `data-integrity-cron.mjs` | Orphan / stale / dup / range / JSON-shape checks; Slack alerts |

### `tests/contracts/`

Endpoint contracts driven by AJV. Each `.json` file is one
endpoint's contract: status code, Content-Type, JSON Schema for
response. Currently 4 bootstrap contracts (liveness, leads,
health/schema, ask); 28 more to add over time. See
`tests/contracts/README.md` for the file shape + how to add new ones.

### Known caveats

- **Preview-smoke + post-deploy-smoke** both expect a Render→GitHub
  `repository_dispatch` translator (Cloudflare Worker / small service).
  Without it, both workflows can only run via `workflow_dispatch` with
  manual inputs. See `docs/post-deploy-smoke.md` for the existing
  forwarder design.
- **`auth-smoke.mjs`** uses a throwaway `TEST_OPERATOR_EMAIL`. If
  `OPERATOR_EMAIL` single-tenancy is enforced in
  `backend/main.py` lifespan, that test user trips the boot assertion.
  Resolve before running in prod.
- **`schema-migration-smoke.mjs`** uses a naive `CREATE TABLE (…);`
  regex parser. Doesn't handle `LIKE` / `PARTITION OF`. Swap to
  `pg-query-emscripten` if the schema gains those.
- **`contract-smoke.mjs`** uses `Ajv({ allErrors: false })` per
  CWE-400 hardening (a hostile upstream could otherwise allocate
  unlimited error objects).
- **OfflineQueue** is in-memory only. A reload between offline and
  online drops the queue.
- **Visual baselines** are OS-sensitive (font rendering). Generate
  baselines on the same OS that CI runs on.

## Pending ops items (queued)

From the 16-spec ops batch, these are unstarted:

- 8.5 — cert + DNS + HSTS + domain expiry (weekly)
- 8.7 — rate-limit smoke (weekly)
- 8.8 — observability smoke (Render Logs API + PII grep)
- 8.9 — backup restore drill (monthly, manual)
- 8.10 — API-key rotation playbook + verifier
- 8.13 — cold-start latency tracking
- 8.14 — disaster-recovery runbook

Also: 28 remaining endpoint contracts under `tests/contracts/`.
