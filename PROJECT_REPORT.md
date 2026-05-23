# LeadDataScraper — Comprehensive Project Report

Generated: 2026-05-22 · Scope: the entire project from zero to one hundred —
what it is, what it contains, how every part works, the goals, the stack, the
security model, the test posture, and the deployment shape.

---

## 0. One-paragraph essence

**LeadDataScraper** is a single-operator lead-generation and
sales-intelligence pipeline. It discovers businesses on Google Maps, scrapes
and audits their websites, enriches each lead with AI-generated business
intelligence, scores them for outreach potential, drafts personalised
outreach (email + LinkedIn) with an LLM, and organises everything into
campaigns — all behind a Next.js dashboard. The operator's job becomes:
type a natural-language instruction ("find 20 dentists in Mostar, audit
them, draft outreach"), confirm the AI's proposed plan, and review the
results. It is built as a **single-tenant** tool — one operator, one
Supabase project — and is hardened accordingly.

---

## 1. The problem it solves & the goals

**Problem.** Cold B2B outreach has three expensive manual steps: (1) finding
prospects, (2) researching each one enough to personalise a pitch, (3)
writing the pitch. Done by hand this is hours per lead.

**Goal.** Collapse all three into an automated pipeline where the operator
supplies intent and the system supplies the labour:

- **Discover** — pull real businesses (name, website, phone, address,
  rating) from Google Maps for a given query + location.
- **Audit** — crawl each lead's website, compute an SEO/health score,
  detect the tech stack, flag vulnerabilities (no SSL, missing title,
  no H1, no analytics).
- **Enrich** — generate business intelligence (offerings, pain points,
  target clients, company summary) and harvest contact details / social
  profiles.
- **Score & segment** — an outreach score (0-100) and a segment label
  ("Performance Optimization", "Low Priority Prospect", …) so the
  operator works the best leads first.
- **Draft** — LLM-written, per-lead personalised email + LinkedIn
  messages that cite the lead's actual audit findings.
- **Orchestrate** — run all of the above as a background pipeline,
  monitored live, stoppable mid-flight.
- **Campaign** — bundle leads into outreach campaigns, generate
  messages, export to CSV for an external sending tool.

**Design constraint.** One operator. No public signup. No multi-tenancy.
This is deliberate — it removes a whole class of cross-tenant authz
complexity, and a boot-time invariant (`OPERATOR_EMAIL`) enforces it.

---

## 2. The load-bearing pipeline

```
operator types intent  ──►  AI router (Gemini)  ──►  proposed plan card
        │                                                   │
        │                                          operator confirms
        ▼                                                   ▼
   Next.js dashboard  ◄──── live job status ◄──── Task Orchestrator
        ▲                                                   │
        │                                    ┌──────────────┼──────────────┐
        │                                    ▼              ▼              ▼
        │                            Discovery Engine  SEO Audit     Enrichment
        │                            (Playwright →     (aiohttp →    (Playwright →
        │                             Google Maps)     site crawl)   site + AI)
        │                                    │              │              │
        └──────── Supabase (leads) ◄─────────┴──────────────┴──────────────┘
```

Every box is covered in detail below.

---

## 3. Top-level repository layout

```
LeadDataScraper/
├── backend/
│   └── main.py                  FastAPI app — all 32 HTTP endpoints
├── src/
│   ├── core/
│   │   ├── agentic_router.py     NL → task plan; executes plans (the "AI brain")
│   │   ├── task_orchestrator.py  background job lifecycle (discovery/audit/hunt/enrich)
│   │   └── parallel_auditor.py   concurrent per-lead audit/hunt with cooperative cancel
│   ├── scrapers/
│   │   ├── discovery_engine.py   Google-Maps lead scrape (Playwright)
│   │   ├── seo_audit.py          website SEO/health audit + tech detection (aiohttp)
│   │   └── enrichment_engine.py  Playwright enrichment + SSRF route guard
│   ├── processors/
│   │   ├── ai_mapper.py          GeminiMapper — CSV header → canonical schema
│   │   ├── google_maps.py        raw Google-Maps export DataFrame cleaning
│   │   └── leadhunter.py         contact/social hunt, outreach scoring, segmentation, hooks
│   ├── utils/
│   │   ├── supabase_helper.py    Supabase client wrapper (service_role)
│   │   ├── csv_helper.py         CSV load/save, dedup, formula-injection sanitiser
│   │   ├── ssrf_guard.py         SSRF defence — scheme + DNS-resolved-IP allowlist
│   │   ├── prompt_safety.py      <UNTRUSTED_DATA> fence + system instruction
│   │   ├── json_helper.py        JSON parsing helpers
│   │   └── logging_config.py     structured logging setup
│   ├── integrations/
│   │   └── email_sender.py       SMTP sender (implemented, not yet wired)
│   └── scripts/
│       └── export_leads.py       CSV export generator (3 export shapes)
├── frontend/                     Next.js 16 dashboard (App Router, React 19, TS)
│   ├── app/
│   │   ├── page.tsx              main dashboard (lead inventory, modals, orchestration)
│   │   ├── insights/page.tsx     analytics + AI strategic analysis
│   │   ├── campaigns/page.tsx    outreach campaign management
│   │   ├── login/{page,actions}.tsx   auth (Server Action sign-in)
│   │   ├── api/proxy/[...path]/route.ts   server-side proxy (injects X-API-Key)
│   │   ├── api/auth/signout/route.ts      sign-out with Origin gate
│   │   └── components/           AIChat, Sidebar, HealthChart, StatsCards, FilterBar …
│   ├── utils/
│   │   ├── apiConfig.ts          apiFetch wrapper, /api/proxy base URL
│   │   ├── loginThrottle.ts      in-process per-IP brute-force gate
│   │   ├── url.mjs               ensureProtocol + sanitizeNext (URL-safety guards)
│   │   └── supabase/             SSR client, middleware, cookie-floor
│   └── proxy.ts                  Next 16 middleware convention (auth redirect)
├── tests/                        16 pytest files (194 tests + 17 subtests)
├── supabase_schema.sql           4 tables + RLS + the add_lead_column RPC
├── Dockerfile                    backend container (Playwright base image)
├── render.yaml                   Render deploy — backend + frontend services
├── requirements.txt              16 pinned Python deps
└── *.md                          CLAUDE.md, BUGS.md, PENTEST_CRAWLER.md, … (docs)
```

---

## 4. Backend — FastAPI (`backend/main.py`)

A single FastAPI app, ~1100 lines, exposing **32 HTTP endpoints**. Every
endpoint except the liveness probe `/` requires the `X-API-Key` header,
validated by the `verify_api_key` dependency with a constant-time compare.

### 4.1 Endpoint inventory (grouped by purpose)

**Liveness & health**
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | unauthenticated liveness probe — returns `{"status":"ok"}`, no metadata |
| GET | `/health/schema` | reports DB column drift (rate-limited 12/min) |

**Leads — read & ingest**
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/leads` | all leads, newest first, capped 200 (30/min) |
| GET | `/stats` | aggregate counts — totals, audit-status & SEO-score distributions |
| POST | `/upload` | CSV upload → AI column mapping → background upsert (5/min, 50 MB cap) |

**Audit pipeline**
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/process-lead` | audit one lead |
| POST | `/process-all` | audit every lead |
| GET | `/audit-status` | poll the running audit |
| POST | `/audit/stop` | stop all running audit jobs |

**AI surface**
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/ask` | natural-language instruction → plan (auto-executes read-only tasks) |
| POST | `/execute` | execute a confirmed plan — Literal task allowlist, `extra='forbid'` |
| GET | `/insights` | AI strategic analysis of the pipeline |
| POST | `/draft-outreach` | LLM email draft for one lead |
| POST | `/draft-linkedin` | LLM LinkedIn message draft for one lead |

**Discovery / hunt / enrichment**
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/discovery/start` | Google-Maps scrape job (5/min) |
| POST | `/hunt-lead` | deep digital hunt for one lead (20/min) |
| POST | `/hunt-all` | hunt all leads missing social data (3/min) |
| POST | `/enrich/start` | enrichment engine for one lead (10/min) |

**Orchestrator (background jobs)**
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/orchestrator/start` | start the massive multi-task pipeline (3/min) |
| GET | `/orchestrator/status/{job_id}` | poll a job (60/min) |
| POST | `/orchestrator/stop/{job_id}` | stop a job |

**Exports**
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/export` | regenerate the CSV export set (6/hour) |
| GET | `/export/download` | download the full-leads CSV |
| GET | `/export/outreach` | download the outreach-ready CSV |

**Campaigns**
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/campaigns` | create a campaign |
| GET | `/campaigns` | list campaigns |
| GET | `/campaigns/{id}` | campaign detail + messages |
| POST | `/campaigns/{id}/generate` | AI-generate per-lead messages |
| POST | `/campaigns/{id}/start` | mark a campaign active |
| POST | `/campaigns/{id}/pause` | pause a campaign |
| GET | `/campaigns/{id}/export` | export campaign messages as CSV |

**Destructive**
| Method | Path | Purpose |
|--------|------|---------|
| DELETE | `/leads/clear` | purge all leads + jobs — requires X-API-Key **and** X-Admin-Token (3/hour) |

### 4.2 Request validation

Every POST body is a Pydantic model with `model_config = ConfigDict(extra="forbid")`
(mass-assignment defence) and bounded-length `constr` fields. Enum-like fields
(`channel`, `status`, the `/execute` task) use `Literal` allowlists so invalid
values are rejected at the boundary. `/ask`'s instruction caps at 4000 chars to
bound the prompt that flows into Gemini.

### 4.3 Error handling

- A global `@app.exception_handler(Exception)` converts any uncaught error
  to `{"error": "Internal server error"}` (500) — never a stack trace.
- `_validation_with_authz_check` gates Pydantic 422 responses behind the
  X-API-Key check, so an anonymous attacker can't probe the body schema.
- Internal error strings are never echoed back to the client.

---

## 5. The AI layer

### 5.1 AgenticRouter (`src/core/agentic_router.py`) — the brain

`route_instruction(text)` sends the operator's natural-language instruction
to Google Gemini together with a **lead index** (up to 200 rows of
unique_key + name + company_name) so the model can resolve "audit Alpha
Tech" → `seo_audit(unique_key=…)`. It returns a **plan**: `{task, params,
reasoning}`. `task` is one of a fixed `Literal` set — `DATABASE_QUERY,
STATUS_CHECK, SEO_AUDIT, OUTREACH_DRAFT, GET_INSIGHTS, DATA_MERGE,
DEEP_HUNT, RUN_MASSIVE_PIPELINE, LINKEDIN_DRAFT, DISCOVERY_SEARCH,
DEEP_ENRICHMENT, CAMPAIGN_STRATEGY` — and the router never emits anything
outside that set, so a prompt-injected fake task name is discarded.

`execute_task(plan)` dispatches to a handler per task. Read-only tasks
(`STATUS_CHECK`, `DATABASE_QUERY`, `GET_INSIGHTS`) auto-execute on `/ask`;
write tasks require an explicit operator "Confirm & Execute".

### 5.2 GeminiMapper (`src/processors/ai_mapper.py`)

`get_column_mapping(headers)` — when a CSV is uploaded with non-canonical
headers (`Business Name`, `Web Address`, `Mail`), Gemini maps them to the
schema (`company_name`, `website`, `email`). The consumer filters out
identity self-maps and coalesces duplicate target columns so no data is
lost.

### 5.3 LeadHunter AI methods (`src/processors/leadhunter.py`)

- `analyze_pain_points_async` — reads scraped page text + audit results,
  produces a pain-point narrative.
- `generate_outreach_hooks_async` — produces the email + LinkedIn "hooks".
- `enrich_business_data_async` — offerings, target clients, summary.
- `calculate_outreach_score` — a 0-100 score from contact availability,
  SEO score, risk flag.
- `segment_lead` — a human-readable segment label.

### 5.4 Prompt-injection defence (`src/utils/prompt_safety.py`)

Every Gemini call that mixes static prompt text with attacker-controllable
data (lead fields from CSV uploads + Google-Maps scrapes, scraped page
bodies) fences that data inside `<UNTRUSTED_DATA>…</UNTRUSTED_DATA>` via
`fenced_json()`, and pairs it with `_UNTRUSTED_DATA_SYSTEM_INSTRUCTION`
which tells the model to treat the fenced content as data, never as
instructions. Any literal `</UNTRUSTED_DATA>` in the payload is stripped
first so an attacker can't close the fence early.

---

## 6. The crawler layer

### 6.1 Discovery Engine (`src/scrapers/discovery_engine.py`)

`find_leads(query, location)` drives a headless Playwright Chromium to
`google.com/maps/search/…` (host hardcoded, query `quote_plus`-encoded —
no host-controlled SSRF). It scrolls to load results, extracts each
result card into `{name, unique_key, website, phone, rating,
audit_status, lead_source: 'google_maps', address}`. `unique_key` is the
stable Google place-ID segment, or a 16-char MD5 of the name as fallback.
Address is pulled from the Maps side panel. A Playwright `route()` guard
re-runs the SSRF check on every subresource and redirect.

### 6.2 SEO Audit (`src/scrapers/seo_audit.py`)

`perform_seo_audit_async(url)` fetches a site through an aiohttp connector
whose DNS resolver is the `SSRFGuardResolver`. It parses the HTML
(BeautifulSoup), checks meta tags / headings / SSL, detects the tech
stack (CMS, infrastructure, tracking pixels, social portals), extracts
emails, and computes a 0-100 SEO score. Internal/SSRF targets are
rejected and surface as a `red_flags` entry, never fetched.

### 6.3 Enrichment Engine (`src/scrapers/enrichment_engine.py`)

Playwright-driven deep enrichment — every browser context installs
`_install_ssrf_route_guard`, closing the TOCTOU window between the
pre-flight DNS check and `page.goto()`, and blocking redirect chains that
hop to an internal host.

### 6.4 Orchestration (`src/core/task_orchestrator.py` + `parallel_auditor.py`)

`TaskOrchestrator.run_massive_pipeline` runs discovery/audit/hunt/enrich
as a background job, tracked in the `orchestration_jobs` table. The
`ParallelAuditor` audits/hunts leads concurrently with a **cooperative
cancel point** between awaits — a `stop_job` call raises `CancelledError`
at the next checkpoint, so a stopped job leaves rows untouched rather
than overwriting them mid-flight.

---

## 7. Database — Supabase / Postgres

Four tables, all in the `public` schema:

### 7.1 `leads` — the central entity
`id` (UUID PK), `unique_key` (TEXT UNIQUE — the dedup key), `name`,
`website`, `email`, `phone`, `address`, `rating` (FLOAT), `reviews`
(INT), `lead_source`, `audit_status`, `audit_results` (JSONB),
`seo_score` (INT), `high_risk_flag` (BOOL), enrichment fields
(`business_details`, `key_offerings`, `pain_points`, `target_clients`,
`leadership_team`, `company_size`, `contact_details`), social columns
(`facebook`, `instagram`, `linkedin`, `tiktok`, `pinterest`),
`outreach_score` (INT), `segment`, `email_hook`, `linkedin_hook`,
`first_name`, `company_name`, `priority_link`, `needs_manual_review`
(BOOL), timestamps.

### 7.2 `orchestration_jobs`
`id` (UUID PK), `status` (starting/running/completed/failed/stopped),
`total_count`, `processed_count`, `current_phase`, `filters` (JSONB),
`created_at`, `updated_at`.

### 7.3 `campaigns`
`id` (UUID PK), `name`, `status` (draft/active/paused/completed),
`channel` (email/linkedin/multi), `segment_filter`, `total_leads`,
`sent_count`, `reply_count`, timestamps.

### 7.4 `campaign_messages`
`id` (UUID PK), `campaign_id` (FK → campaigns, ON DELETE CASCADE),
`lead_unique_key` (FK → leads), `channel`, `subject`, `body`,
`status` (pending/sent/delivered/replied/bounced), `sent_at`,
`created_at`.

### 7.5 RLS posture
All four tables have RLS **enabled**, all privileges **revoked** from
`anon` + `authenticated`, and an explicit `*_deny_all` policy
(`USING (false) WITH CHECK (false)`). All real reads/writes go through
the backend, which uses the `service_role` key (bypasses RLS
server-side). Schema migrations use the narrow `add_lead_column(text)`
RPC (allowlisted column-name regex, `SET search_path = pg_catalog,
public`, `OWNER TO postgres`, `EXECUTE` revoked from `anon`). The
generic `exec_sql` RPC was removed.

### 7.6 DB-level integrity gates

Pydantic at the FastAPI boundary already validates writes, but a leaked
`service_role` key or a Supabase Studio operator bypasses it. The DB
enforces the same invariants independently:

- **10 named `CHECK` constraints** (`supabase_schema.sql`): scores
  0..100 with NULL allowed; audit_status / enrichment_status /
  orchestration_jobs.status / campaigns.status /
  campaign_messages.status enums; channel enums on
  `campaigns`/`campaign_messages`; loose email shape
  (`length>=3 AND LIKE '%@%'`). Wide audit_status allowlist mirrors
  current producer reality (`parallel_auditor.py` writes error-reason
  strings into the status slot — refactor tracked separately).
- **Per-role `statement_timeout`** (`ALTER ROLE ... SET ...`):
  `anon`=3s, `authenticated`=8s, `service_role`=30s. Long-running
  query DoS guard at the role level.
- **Hot-path indexes**: `idx_leads_created_at_desc`,
  `idx_leads_audit_status`, `idx_orchestration_jobs_status`,
  `idx_campaign_messages_lead_unique_key` — added to make the
  dashboard's "top 200 by created_at DESC" and "by audit_status"
  queries land on index scans even on small row counts.
- **`pg_advisory_xact_lock` namespace `0x4EAD`** — documented
  serialization key for any code path that does read-modify-write on a
  lead (e.g. ParallelAuditor + manual UI edit). Concurrency tests
  prove both the lost-update window and the lock's fix; the lock has
  not been adopted in `ParallelAuditor` yet — tracked as future
  cleanup.
- **`add_check_constraints` + `add_missing_perf_indexes` +
  `set_service_role_statement_timeout`** migrations applied to live
  DB; all mirrored in `supabase_schema.sql` via idempotent
  `DO $$ ... duplicate_object` blocks.

Every constraint, policy, role, function, index, and timeout is
re-verified by CI (see section 11).

---

## 8. Frontend — Next.js 16 dashboard

### 8.1 Pages
- **`/` (dashboard)** — lead inventory table, health pie-chart, 4 stat
  cards, filter bar, orchestration buttons (Audit All, AI Orchestrate,
  Hunt All), Import CSV, Settings + Discovery modals, the floating AI
  chat. Wrapped in `<Suspense>` for the `useSearchParams` cross-page
  nav contract.
- **`/insights`** — analytics + AI strategic analysis, Recharts
  visualisations.
- **`/campaigns`** — campaign list + detail, message generation, CSV
  export.
- **`/login`** — Server-Action sign-in.

### 8.2 The proxy — why the browser never holds the API key
The browser calls a same-origin Next.js route `/api/proxy/[...path]`.
That server-side route injects `X-API-Key` (and, only on the
`leads/clear` path, `X-Admin-Token`) from server-only env vars, strips
client-supplied forwarding headers, re-emits a trusted `X-Forwarded-For`,
caps the body at 50 MB, stamps `Cache-Control: no-store`, and asserts the
backend URL is HTTPS in production. The API key is never in the browser
bundle.

### 8.3 Auth
`proxy.ts` (Next 16 middleware convention) redirects anonymous traffic to
`/login`. Sign-in is a Server Action calling Supabase
`signInWithPassword`; the session cookie is true-floored to
`HttpOnly + SameSite=Lax + Secure(prod)` by `cookie-floor.mjs`. An
in-process per-IP throttle (`loginThrottle.ts`, 5/60s) sits in front of
the credential check.

---

## 9. Security model (layered defence)

| Layer | Mechanism |
|-------|-----------|
| Transport | HSTS (prod, 2y preload), HTTPS-only backend assertion |
| Browser | CSP (`script-src 'self'` in prod), X-Frame-Options DENY, X-Content-Type-Options, Referrer-Policy, Permissions-Policy |
| Auth | Supabase session, httpOnly+SameSite cookie floor, brute-force throttle, single-tenant boot invariant |
| API | `X-API-Key` constant-time check on every endpoint; `X-Admin-Token` second factor on destructive ops |
| CSRF | fail-closed Origin allowlist on state-changing POSTs |
| Input | Pydantic `extra='forbid'` + Literal allowlists + bounded `constr` |
| Injection | `<UNTRUSTED_DATA>` prompt fence; CSV formula-injection sanitiser; SMTP CRLF guard; PostgREST parametrisation |
| SSRF | `ssrf_guard` — scheme + DNS-resolved-IP allowlist, cloud-metadata hostname denylist, Playwright route re-check |
| Database | RLS deny-all + REVOKE on anon/authenticated; service_role server-only; 10 named CHECK constraints (range + enum + email shape); per-role `statement_timeout` (3/8/30s) |
| Rate limiting | slowapi per-endpoint caps; XFF honoured only with a valid API key |
| Supply chain | pinned deps, lockfile integrity, CI `pip-audit` + `npm audit` + Semgrep + gitleaks (full-history) with fork-PR guard |
| DB drift detection | 13 CI gates (`schema-drift`, `referential-integrity`, `query-plans`, `jsonb-shapes`, `null-audit`, `orphans-zombies`, `statement-timeouts`, `grants-matrix`, `function-safety`, `analyze-freshness`, `db-bloat`, `slow-queries`, `storage-monitor`) running on PR + daily cron — catches any Supabase Studio hand-edit, broken FK, lost index, drifted enum, or zombie job |
| Backup verification | `backup-verify-deep.yml` workflow (disabled-by-default) restores a Supabase branch to `now() - 1h`, runs schema-drift + integrity + row-count diff, records RTO. Pro plan + access token required to enable |

**Penetration testing.** Four pentest rounds (documented in
`PENTEST_CRAWLER.md`) — crawler ingestion, OWASP API Top 10, session/JWT/
race conditions, and a direct Supabase attack bypassing the backend —
plus a 62-scenario AI audit (`AI_SCENARIO_TEST.md`). **Zero exploitable
vulnerabilities**; one robustness defect (a 500 on a malformed `job_id`)
was found and fixed.

---

## 10. Tech stack

**Backend (Python)** — FastAPI 0.121, uvicorn, Supabase 2.29,
google-genai 1.12, Playwright 1.50, aiohttp 3.13, BeautifulSoup4,
pandas 2.2, slowapi (rate limiting). 16 pinned dependencies.

**Frontend** — Next.js 16.2 (App Router, Turbopack), React 19.2,
TypeScript 5, Recharts 3, Lucide icons, `@supabase/ssr` + `@supabase/
supabase-js` (exact-pinned — security-critical libs).

**Database / infra** — Supabase (Postgres 17, PostgREST, Auth),
deployed on Render (backend as a Docker service off the Microsoft
Playwright base image, frontend as a Node service).

---

## 11. Testing

18 pytest files — **194 unit tests + 17 subtests + 2 live-DB integration
suites (concurrency + connection pool), all passing**. Unit coverage:
the AgenticRouter, the `/execute` plan model (Literal allowlist,
`extra='forbid'`), CSV helper health, the security helpers
(`sanitize_csv_cell`, `_coalesce_duplicate_columns`, `_is_valid_uuid`),
the SMTP header-injection guards, the SSRF guard core logic, the 422
schema-leak gate, CORS, the Supabase helper, logging, robustness, and the
prompt-injection fence.

**Live-DB integration:**
- `tests/test_concurrent_writes.py` — 20 concurrent UPDATEs converge
  (row-lock), 20 concurrent INSERTs same `unique_key` produce 1 OK +
  19 UniqueViolation, UPDATE + DELETE converge to no-row, lost-update
  window documented, `pg_advisory_xact_lock` serialization verified.
- `tests/test_connection_pool.py` — static grep asserts backend has no
  direct Postgres driver import (PostgREST-only invariant), DATABASE_URL
  targets the pooler endpoint, 20 concurrent connections succeed.

**Database CI gates** (push + daily cron via `security.yml`; subset also
PR-time-blocking via `ci.yml`):
`schema-drift`, `referential-integrity`, `query-plans`, `jsonb-shapes`,
`null-audit`, `orphans-zombies` (with auto-heal for zombie
orchestration_jobs >4h), `statement-timeouts`, `grants-matrix`,
`function-safety`, `analyze-freshness`, `db-bloat`, `slow-queries`,
`jsonb-index-suggestions`, `storage-monitor`. Two workflow skeletons
shipped disabled-by-default for when the project moves to Supabase Pro:
`backup-verify-deep.yml` (monthly PITR-restore drill) and
`migration-safety.yml` (preview-branch verification on schema-touching
PRs).

The frontend has `node --test` suites — 50 tests across the
cookie-floor and URL-safety (`ensureProtocol`, `sanitizeNext`) guards.
CI runs `pip-audit`, `npm audit`, Semgrep, and `gitleaks` (full git
history) on every push.

---

## 12. Project state (as of this report)

- Backend: ~1100-line FastAPI app, 32 endpoints, 11 src modules.
- Frontend: Next.js 16, ~49k lines TS/TSX (incl. one large dashboard
  component), 4 pages, ~8 components.
- Database: 4 tables, RLS-hardened, 10 named CHECK constraints, 7
  indexes (incl. 3 hot-path), per-role statement_timeout, 13 CI gates
  on Supabase Postgres 17.
- Tests: 194 pytest unit + 2 live-DB integration suites + 50 frontend
  = ~250 automated tests, all green.
- CI: 14 jobs in `ci.yml` (PR-time, block merge) + 14 jobs in
  `security.yml` (push + daily cron) + 2 disabled-by-default
  workflow skeletons for Supabase-Pro features.
- Security: 4 pentest rounds + AI scenario audit + 13 DB drift gates,
  0 open vulnerabilities; layered defences documented in `CLAUDE.md`
  + `SECURITY.md`.
- Docs: `CLAUDE.md` (the canonical architecture + invariants),
  `SECURITY.md` (the trust model + the gate catalogue), `BUGS.md` (4
  closed bug rounds), `PENTEST_CRAWLER.md`, `AI_SCENARIO_TEST.md`,
  `E2E_TEST_PLAN.md`, this report.

**In one sentence:** LeadDataScraper is a hardened, single-operator,
AI-driven B2B lead pipeline — discover, audit, enrich, score, draft,
campaign — with a FastAPI/Playwright/Gemini backend, a Next.js dashboard
that never touches the API key, a Supabase database locked down
independently of the backend, and a security posture verified by four
penetration-test rounds.
