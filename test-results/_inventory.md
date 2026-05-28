# Inventory — surfaces under test

_Generated 2026-05-28. Re-run inventory cells in `scripts/aggregate_test_results.py`
will NOT refresh this — it is a manual point-in-time map._

## 1. Deployed surfaces

| Surface  | URL                                                      | Notes                                          |
|----------|----------------------------------------------------------|------------------------------------------------|
| Frontend | https://lead-scraper-frontend.onrender.com               | Next 16, redirects `/` → `/login?next=/`       |
| Backend  | https://lead-scraper-backend-x51l.onrender.com           | FastAPI; `/` returns `{"status":"ok"}`         |
| Sweeper  | (cron, no HTTP surface) `lead-scraper-webhook-sweeper`   | Background only                                |

Render plan = `starter` (no auto-sleep). Cold-start probes 2026-05-28:

| Target           | Probe 1 (cold) | Probe 2 (warm) | HTTP | Final URL                                   |
|------------------|----------------|----------------|------|---------------------------------------------|
| backend `GET /`  | 0.300 s        | 0.330 s        | 200  | —                                           |
| frontend `GET /` | 0.826 s        | 0.884 s        | 200  | `/login?next=%2F` (one 307 → 200)           |

Both surfaces returned 200 on the **first** probe — no warm-up needed for timing tests.
TLS connect ~26 ms (HTTP/2, edge in `iad1`-ish region). CSP nonce present in login HTML.

## 2. Frontend routes (Next 16 App Router)

| Route               | File                              | Auth   | Notes                                         |
|---------------------|-----------------------------------|--------|-----------------------------------------------|
| `/`                 | `app/page.tsx`                    | gated  | Dashboard; consumes `?openSettings/?openDiscovery/?view/?search` |
| `/login`            | `app/login/page.tsx`              | public | `?next=` sanitised by `sanitizeNext()`        |
| `/insights`         | `app/insights/page.tsx`           | gated  | Recharts panels (lazy)                        |
| `/campaigns`        | `app/campaigns/page.tsx`          | gated  | Outreach campaigns                            |
| `/unsubscribe/{tk}` | backend HTML response (not Next)  | public | Tight per-route CSP via `_UNSUB_HTML_HEADERS` |

Layout: `app/layout.tsx` is the single root layout (`force-dynamic`, threads CSP nonce).

### Next route handlers (API surfaces inside frontend)

| Handler                       | File                                          | Notes                                                            |
|-------------------------------|-----------------------------------------------|------------------------------------------------------------------|
| `/api/auth/signout`           | `app/api/auth/signout/route.ts`               | POST same-origin; fail-closed Origin allowlist                    |
| `/api/proxy/[...path]`        | `app/api/proxy/[...path]/route.ts`            | Injects `X-API-Key`, strips upstream `Server`, `Cache-Control: no-store`. `PUBLIC_PROXY_PATHS={metrics}` skips Supabase re-check. |

Middleware: `frontend/proxy.ts` (Next 16 convention) wraps `utils/supabase/middleware.ts`.
Public allowlist: `/login`, `/auth`, `/api/auth`, `/api/proxy/metrics`, `/monitoring`.

## 3. Components

`frontend/app/components/` (11 files, all `.tsx`):

| Component             | Interactive surface                                 |
|-----------------------|------------------------------------------------------|
| AIChat.tsx            | 8 `<button>` — submit / send / abort / Confirm-plan |
| BrandIcons.tsx        | (icon set; no interactives)                          |
| FilterBar.tsx         | 2 `<button>` + select(s)                            |
| HealthChart.tsx       | recharts (lazy)                                      |
| InsightsCharts.tsx    | recharts (lazy)                                      |
| LeadTable.tsx         | 8 `<button>` — row actions, Load-more, drawer       |
| LocaleSwitcher.tsx    | locale dropdown                                      |
| OfflineBanner.tsx     | `useSyncExternalStore` (PR #353 refactor)           |
| Sidebar.tsx           | 8 `<button>` — modal triggers (settings, discovery, …) |
| StatsCards.tsx        | counters                                             |
| WebVitalsReporter.tsx | renders nothing (effect-only beacon)                |

There is NO `frontend/components/` dir — all components live under `frontend/app/components/`.

## 4. Interactive element census (frontend/app/, `.tsx` only)

| Element            | Count | Top files                                                                                       |
|--------------------|-------|--------------------------------------------------------------------------------------------------|
| `<button>` / `<Button>` | 72 | `page.tsx` 32, `campaigns/page.tsx` 12, `Sidebar.tsx` 8, `LeadTable.tsx` 8, `AIChat.tsx` 8, `FilterBar.tsx` 2, `login/page.tsx` 1, `insights/page.tsx` 1 |
| `role="dialog"` / `<dialog>` / `<Modal>` | 5  | (mostly `page.tsx`)                                                            |
| `onClick=` handlers | 82   | (matches expectation given 72 buttons + row delegation)                                           |
| `<input>` / `<select>` / `<textarea>` | 17 | login form + filter + AI chat + upload                                                     |
| `<form>`            | 3    | login, AI chat, settings                                                                         |
| `<a href>` / `<Link>` | 12  | nav + outreach `mailto:`                                                                         |

Interpretation for terminals: "every button" = walk the 72-row file matrix above
(not every `<button>` in node_modules). Component-level coverage = the 11 files
in §3.

## 5. Backend API endpoints (`backend/main.py`, 42 routes)

| Method | Path                                  | Notes                                            |
|--------|---------------------------------------|--------------------------------------------------|
| GET    | `/`                                   | health, `{"status":"ok"}` — only unauth route    |
| POST   | `/_sentry/test`                       | env-gated `SENTRY_TEST_ENABLED=1`                |
| POST   | `/metrics`                            | WebVitals beacon                                 |
| GET    | `/unsubscribe/{token}`                | public HTML                                      |
| POST   | `/unsubscribe/{token}`                | public HTML form                                 |
| POST   | `/webhooks/instantly`                 | HMAC verified                                    |
| GET    | `/leads`                              | cursor pagination                                |
| POST   | `/upload`                             | 50 MB cap, CSV/xls only                          |
| POST   | `/process-lead`                       |                                                  |
| POST   | `/process-all`                        |                                                  |
| GET    | `/audit-status`                       |                                                  |
| POST   | `/audit/stop`                         |                                                  |
| GET    | `/health/schema`                      |                                                  |
| POST   | `/ask`                                | AI router auto-execute read-only                 |
| GET    | `/insights`                           |                                                  |
| GET    | `/stats`                              | 60 s TTL                                         |
| POST   | `/draft-outreach`                     |                                                  |
| POST   | `/draft-linkedin`                     |                                                  |
| POST   | `/execute`                            | `extra='forbid'`                                 |
| POST   | `/hunt-lead`                          |                                                  |
| POST   | `/hunt-all`                           |                                                  |
| POST   | `/discovery/start`                    |                                                  |
| POST   | `/enrich/start`                       |                                                  |
| DELETE | `/leads/clear`                        | **+X-Admin-Token**                               |
| DELETE | `/leads/demo`                         | **+X-Admin-Token** + Pydantic Literal `REMOVE DEMO` |
| DELETE | `/operator/account`                   | **+X-Admin-Token** + Pydantic Literal `DELETE MY ACCOUNT`. Article 17 erasure |
| POST   | `/orchestrator/start`                 |                                                  |
| GET    | `/orchestrator/status/{job_id}`       |                                                  |
| GET    | `/orchestrator/active`                |                                                  |
| GET    | `/operator/data-export`               | Article 20 export; 1/day peer-IP                  |
| POST   | `/orchestrator/stop/{job_id}`         |                                                  |
| GET    | `/export`                             |                                                  |
| GET    | `/export/download`                    | streaming CSV                                    |
| GET    | `/export/outreach`                    | streaming CSV                                    |
| POST   | `/campaigns`                          |                                                  |
| GET    | `/campaigns`                          |                                                  |
| GET    | `/campaigns/{campaign_id}`            |                                                  |
| POST   | `/campaigns/{campaign_id}/generate`   |                                                  |
| POST   | `/campaigns/{campaign_id}/start`      |                                                  |
| POST   | `/campaigns/{campaign_id}/pause`      |                                                  |
| GET    | `/campaigns/{campaign_id}/export`     |                                                  |
| GET    | `/admin/gemini-budget`                | **+X-Admin-Token**                               |

All routes except `GET /` and `/{unsubscribe,webhooks/instantly}` require `X-API-Key`.
The three DELETE routes + `/admin/gemini-budget` additionally require `X-Admin-Token`
(proxy `ADMIN_TOKEN_PATHS` allowlist; clients cannot set the header).

## 6. Terminal-prefix → API surface map (for terminal 6 sequencing)

- `API-001..` → `GET /`, `GET /health/schema`
- `API-010..` → `/leads`, `/stats`, `/insights`, `/audit-status`, `/orchestrator/*`
- `API-030..` → `/execute`, `/ask`, `/draft-outreach`, `/draft-linkedin`
- `API-040..` → `/process-lead`, `/process-all`, `/hunt-lead`, `/hunt-all`, `/discovery/start`, `/enrich/start`
- `API-050..` → `/upload`, `/export*`
- `API-070..` → `/campaigns*`
- `API-080..` → `/webhooks/instantly`, `/unsubscribe/{token}`
- `API-090..` → `DELETE /leads/clear`, `DELETE /leads/demo`, `DELETE /operator/account`, `/admin/gemini-budget`
- `API-100..` → `/_sentry/test`, `/metrics`

## 7. Auth context for UI terminals

The Lead Scraper Supabase project (`kbtkxpvchmunwjykbeht`) is **single-tenant** —
`OPERATOR_EMAIL` env pins exactly one auth user. Lifespan boot assertion
`_assert_single_tenant_if_enforced()` aborts if a second user lands in
`auth.users`. **Do NOT sign up a fresh QA account on prod** — it will permanently
brick the operator's boot.

See `test-results/_test_account.md` (uncommitted) for the chosen strategy.
For backend-only terminals (API-*) use `X-API-Key` directly to localhost or
`/api/proxy` — that path needs no Supabase session.
