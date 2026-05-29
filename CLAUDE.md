# LeadDataScraper

## Project Overview
Lead data scraping and enrichment pipeline with Supabase backend and Next.js dashboard frontend.

## Tech Stack
- **Backend**: Python, FastAPI, Supabase (database), Playwright, Google GenAI
- **Frontend**: Next.js (App Router), React 19, TypeScript, Recharts, Lucide icons

## Backend Architecture
Per-module dossier → [`docs/architecture/backend-modules.md`](docs/architecture/backend-modules.md). Load-bearing facts:
- `backend/main.py` lazy module-level singletons (`db`, `router`, `auditor`, `orchestrator`) via module `__getattr__`. **PEP 562 trap**: doesn't fire for bare-name `LOAD_GLOBAL` inside same-module fns. Lifespan primes via `sys.modules[__name__]`. New lazy singleton MUST land in the priming loop AND every cron-callable handler (`_process_instantly_event` shipped PR #415 `d922b334` 2026-05-29 — one `sys.modules[__name__].db` line at function entry; subprocess-isolated regression at `tests/test_webhook_cron_pep562.py`). See `docs/runbooks/pep562-cron-path-trap.md`.
- `src/scrapers/enrichment_engine.py` shared-Chromium pool, per-lead `new_context()`; `aclose()` MUST run on teardown (orchestrator `_process_in_chunks` `finally`).
- `src/utils/supabase_helper.py` (`SUPABASE_SERVICE_ROLE_KEY`) + `stats_cache.py` (60s TTL + `asyncio.Lock`) + `query_profiler.py` (env-gated `QUERY_PROFILER=1`).

## API Security — invariants

Full rationale + test pin per rule: [`docs/api-security-invariants.md`](docs/api-security-invariants.md). Sub-section quick-reference below.

### Auth + transport
- Frontend requires Supabase Auth session. `frontend/proxy.ts` (Next 16; wraps `utils/supabase/middleware.ts`) redirects anon → `/login`. **Do not** create `frontend/middleware.ts` (Next 16 duplicate-convention error). `/api/proxy/[...path]` re-runs `auth.getUser()` → 401. State-changing methods reject foreign `Origin`. No public signup.
- Public-path allowlist (`/login`, `/auth`, `/api/auth`, `/api/proxy/metrics`, `/monitoring`): exact-match-or-trailing-slash-subpath, NOT raw `startsWith`.
- `/login?next=` sanitised by `sanitizeNext()` in `frontend/utils/url.mjs`: same-origin relative only; regex excludes `@`+`:`; decode-once layer rejects `//`/`\`/`..`/controls in decoded form; malformed encoding → `/`. `ensureProtocol()` is `<a href>` scheme guard. CI: `url.test.mjs` (57) + `tests/test_open_redirect.py`.
- Supabase cookies true-floored to `SameSite=Lax`, `HttpOnly`, `Secure` (prod) in `setAll()`. Spread `{...options, sameSite, httpOnly, secure}` — can tighten, not loosen. Fuzz: 1157 cases.
- All endpoints except `/` require `X-API-Key` (`secrets.compare_digest`). `/` returns `{"status":"ok"}` only.
- `/execute` accepts `Literal` allowlist (`ExecutableTask`) + typed `ExecutePlanParams` (bounded `constr` + `extra='forbid'`). Handler dicts via `model_dump(exclude_none=True)`. Pinned: `test_execute_plan_model.py`.
- `/api/proxy` + `/api/auth/signout` apply fail-closed Origin allowlist on POST (mismatched AND missing).
- Optional `OPERATOR_EMAIL` single-tenancy assertion in lifespan (fail-closed: only `RuntimeError` swallowed; any other Auth API failure aborts boot).
- Interactive docs (`/docs`, `/openapi.json`, `/redoc`) disabled. `ENABLE_DOCS=true` for dev. Never prod.
- **Frontend does NOT hold API key.** `/api/proxy/[...path]` injects `X-API-Key` server-side from `API_SECRET_KEY`. Stamps `Cache-Control: no-store` on every response. Strips upstream `Server`.
- Destructive `DELETE /leads/clear` + `/leads/demo` + `/operator/account` + `/admin/gemini-budget` also require `X-Admin-Token` (`ADMIN_TOKEN`). Proxy `ADMIN_TOKEN_PATHS` allowlist (exact-match on joined dynamic segments). Clients cannot set the header.
- Phase 13.3 demo-data: `leads.is_demo BOOLEAN NOT NULL DEFAULT FALSE` + partial index. `seed_demo_data.py` 20 Croatian leads, `.demo.invalid` TLD. `/leads` + `/stats` accept `?include_demo`. `_compute_stats` cache covers exclude-demo only. `_get_strategic_insights` filters `is_demo=false` on both sample + count queries. Frontend toggle: `localStorage['lds-include-demo']`. Danger Zone "Remove all demo data" requires `REMOVE DEMO` (Pydantic Literal). Cascade: campaign_messages → leads.
- Env vars: `.env.example`. **Render parity**: `render.yaml` MUST declare `ALLOWED_ORIGINS` + `ADMIN_TOKEN` envVars (`sync: false`) — else prod state-change 403s.
- Rate-limit via slowapi (`headers_enabled=False`). Key = XFF (only when X-API-Key valid) else peer IP. Proxy strips client XFF/X-Real-IP/Forwarded; re-emits from `TRUSTED_CLIENT_IP_HEADER` (default `x-vercel-forwarded-for`; `x-forwarded-for` on Render).

### Browser security headers
- **CSP per-request** in `frontend/proxy.ts` (NOT static in `next.config.ts`) so `script-src` carries fresh `'nonce-<n>'` + `'strict-dynamic'`. Static `script-src 'self'` would block Next 16 RSC inline bootstrap blocks in prod (sev-1, `docs/findings/2026-05-22-csp-blocks-prod-hydration.md`). Nonce flow: proxy generates 16-byte base64 nonce on a **NEW `Headers` object** (mutating `request.headers` in-place doesn't propagate to RSC — pass via `NextResponse.next({ request: { headers } })`); `middleware.ts::updateSession` threads `requestHeaders`; `layout.tsx` `dynamic = 'force-dynamic'` calls `(await headers()).get('x-nonce')` so Next auto-stamps inline `__next_f` blocks. Without `force-dynamic`, static prerender = no nonce = CSP rejects hydration.
- Static headers in `next.config.ts`: HSTS (2y + preload), XFO DENY, XCTO, Referrer-Policy, Permissions-Policy (cam/mic/geo off), **COOP/CORP/XPCDP** (PR #237). `productionBrowserSourceMaps: false`. CSP also: `default-src 'self'`, `connect-src 'self' <SUPABASE_URL>` + `wss:`, `img-src 'self' data: blob: <SUPABASE_URL>` (no blanket `https:`), `base-uri 'self'`, `form-action 'self'`, `frame-ancestors 'none'`, `object-src 'none'`, `style-src 'self' 'unsafe-inline'`.
- HTML routes (`/`, `/login`, `/insights`, `/campaigns`) get `Cache-Control: private, no-store` + `Vary: Cookie` (pageNoCacheHeaders). Opts out of bfcache so post-signout Back doesn't render cached authed shell. `_next/static/*` excluded.

### Input boundary guards
- `/upload` streams body; 50 MB cap (`MAX_UPLOAD_BYTES`) → 413. Content-Type allowlist: `text/csv` + `application/vnd.ms-excel` only. `application/octet-stream` removed.
- **CSV / formula injection**: `sanitize_dataframe_for_csv()` in `src/utils/csv_helper.py` prefixes cells starting with `=`/`@`/`+`/`-`/`\t`/`\r` with `'`. Applied at every `to_csv` site + streaming exports via inline `_csv_cell`.
- **SMTP header injection**: recipient regex `^[^@\s]+@[^@\s]+\.[^@\s]+\Z` — `\Z` not `$` (Python `$` matches before trailing `\n`). Subject + from_name CRLF-rejected before MIME write. Pinned: `tests/test_crlf_injection.py` + `test_email_sender_guards.py`.
- **Log-line forgery**: `_CRLFScrubFilter` in `src/utils/logging_config.py` scrubs CR/LF/VT/FF in `record.msg`, every entry of `record.args` (tuple + dict), AND any non-reserved `extra={}` key.
- **Email-extraction input cap 50 KB** before `re.findall` — legacy email regex is O(n²) on attacker HTML. Static-scan test fails CI if new call site lands without `[:N]` slice.
- **Control / format-char rejection in user-facing strings**: `safe_constr(...)` in `src/schemas/sanitized_str.py` is a drop-in for `pydantic.constr(...)` that ALSO rejects NUL + `unicodedata.category Cc/Cf` (except `\t \n \r`) via an `AfterValidator` raising `PydanticCustomError`. Plain `ValueError` puts the exception object in `ctx.error` → starlette JSON encoder 500s. Applied to all 20 user-facing `constr(...)` sites across 9 request models in `backend/main.py` (PipelineFilters / Campaign{Create,Update} / LeadProcessRequest / AskInstruction / DiscoveryRequest / PipelineRequest / ExecutePlanParams / WebVitalsMetric). Closes `500 → 422` gap (API-127 + API-201 in `test-results/06-backend-api.md`). Pinned by `tests/security/test_control_char_rejection.py` (3-layer: unit + Pydantic-model + HTTP against real ASGI app).

### Phase 14/15 dispatch + webhook hardening (post-audit 2026-05-27)
- **`/unsubscribe/{token}` HTML response** stamps tight CSP via `_UNSUB_HTML_HEADERS` (`default-src 'none'; form-action 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'`). XFO=DENY already covered by `_security_headers_middleware`; CSP is defense-in-depth for future drift.
- **Webhook field CRLF scrub**: `_STRIP_CTRL_PATTERN = re.compile(r"[\r\n\v\f\x00]")` applied to `event_type` (64), `provider_msg_id` (200), `recipient_email` (320), `lds_message_id` (64), `bounce_reason` (200) at `_process_instantly_event` + `_instantly_handle_bounced`. Belt-and-braces against future compromised-Instantly threat (HMAC verify is primary defense).
- **Instantly dispatcher SSRF guard** (`src/integrations/instantly_sender.py:343`): `await assert_safe_url(url)` runs BEFORE `session.post` so TLS handshake + Authorization header never reach private/metadata IPs. Forward-compat for future `INSTANTLY_BASE_URL` env-configurability.
- **Sequence variant `content_type`** (`'text'` default, `'html'` opt-in): Jinja2 autoescape routed by `thread_builder` reading `variant.content_type`. Attacker-controlled lead fields (`pain_point`, `first_name`, `company`, `industry`, `city` — from CSV ingest + Gemini enrichment of scraped sites) cannot break out of HTML context in recipient mail client. DB CHECK `sequence_variants_content_type_allowed` IN (`text`,`html`) + service-layer + repo-layer validation = 3-deep enforcement.
- **`/api/proxy/[...path]` `PUBLIC_PROXY_PATHS`**: exact-match `Set({'metrics'})` skips Supabase session re-check for the WebVitals beacon (fires pre-login). Origin gate on non-safe methods STILL applies. Backend `/metrics` still requires `X-API-Key` + Pydantic `WebVitalsMetric` `extra='forbid'` + slowapi 60/min.

### SSRF + AI prompt-safety
- Outbound HTTP from `seo_audit.py` + `enrichment_engine.py` + **`instantly_sender.py`** through `src/utils/ssrf_guard.py::assert_safe_url` — rejects private/loopback/link-local/reserved/multicast IPs + cloud/k8s metadata (`metadata.google.internal`, `169.254.169.254`, `kubernetes.default.svc`, `.cluster.local`) at DNS-resolve time. Hardens SSRF + DNS-rebinding.
- Playwright contexts install `_install_ssrf_route_guard(context)` — re-runs `assert_safe_url` on every request (initial nav, 30x redirects, subresources). Closes TOCTOU.
- **Every Gemini call mixing static prompt + DB/scrape data MUST fence in `<UNTRUSTED_DATA>...</UNTRUSTED_DATA>`** + shared `_UNTRUSTED_DATA_SYSTEM_INSTRUCTION`. Use `_fenced_json()` in `src/core/agentic_router.py`. **Strip literal `</UNTRUSTED_DATA>` from payload before embedding** — JSON doesn't escape angle brackets. Never splice lead fields into prompt body text — use `[COMPANY NAME]` placeholders.
- AI clients (`GeminiMapper`, `AgenticRouter`, `LeadHunter`) read `GEMINI_API_KEY` in `__init__`. **Never mutate `os.environ` at request time** — multi-worker race.

### Supabase + database
- **11 RLS-protected tables** (deny-all RESTRICTIVE + REVOKE anon/authenticated/PUBLIC): 5 core (`leads`, `campaigns`, `campaign_messages`, `orchestration_jobs`, `account_deletions`) + Phase 14/15 (`suppressions`, `webhook_events`, `sequences`, `sequence_steps`, `sequence_variants`, `email_send_ledger`). Backend uses `service_role`.
- Schema migrations via `add_lead_column(text)` RPC (allowlisted regex). `exec_sql` removed. `SECURITY DEFINER`, owner postgres, `SET search_path = pg_catalog, public`. `REVOKE CREATE ON SCHEMA public FROM PUBLIC`.
- **16 DB invariant gates** in `src/scripts/` (run in `ci.yml` + `security.yml`): schema drift, referential integrity, hot-path indexes (5 probes), **17 CHECK constraints** (post-2026-05-27 hardening: `sequence_variants_body_size` body≤16384/subject≤998, `sequence_variants_content_type_allowed` ∈ text|html, `webhook_events_event_id_size` 1..256, `sequence_steps_window_ordered`, `sequence_steps_send_days_format` regex allowlist, `campaign_messages_bounce_reason_size` ≤200), JSONB shape, NULL audit, orphan + zombie sweep (1 auto-heal on `running > 4h`), concurrency tests, per-role `statement_timeout` (anon 3s / authenticated 8s / service_role 30s), connection pool, DB bloat, slow query, grants matrix, function safety, ANALYZE freshness, JSONB GIN suggestions, storage size + WoW growth, deep PITR (disabled), migration safety (disabled). Catalog: [`docs/db-invariants.md`](docs/db-invariants.md). **CHECK pairing rule**: new CHECK in `supabase_schema.sql` REQUIRES same-PR `EXPECTED_CHECK_CONSTRAINTS` dict update in `schema_drift_check.py` (3 PRs fell in: #353/#356/#366; codified by #380/#378).

### Error handling
- CORS: `GET/POST/PUT/DELETE/OPTIONS` + `Content-Type/Authorization/X-API-Key` only.
- All POST endpoints Pydantic-validated. Error responses never leak exception details.
- Global FastAPI handler → 500 JSON. **`RecursionError` → 413 "Payload nesting too deep"**. `RequestValidationError` 422 gated behind X-API-Key: anon → generic 403; authed → full `detail[]`. `input` stringified `json.dumps(default=str, allow_nan=False)` + capped 512 chars. Pinned: `tests/test_validation_authz_gate.py` + `test_json_pollution.py`.
- Single-row lookups use `.maybe_single()` not `.single()` (`.single()` raises `APIError(PGRST116)` on 0 rows → swallowed into 500).
- AgenticRouter-delegate handler pattern: check `db.client` → 503; inspect `result["error"]` → `error_response(error, 503)`. Reference `/insights` at `backend/main.py:498-513`.
- `/api/auth/signout` POSTs same-origin; `try { … } finally { router.replace('/login'); router.refresh() }`.
- `hashlib.md5` in `discovery_engine.py` annotated `usedforsecurity=False` (non-crypto unique_key fallback).
- Fingerprint: uvicorn `--no-server-header`; proxy strips upstream `Server`.
- Dockerfile: `build-essential` install + purge same RUN; `HEALTHCHECK` polls `/`.
- Backend `_security_headers_middleware` (PR #238) stamps `X-Frame-Options: DENY` + `X-Content-Type-Options` + `Referrer-Policy` via `setdefault`. CSP + HSTS omitted (Render edge / no HTML).

### GDPR
- **Article 20 export** at `GET /operator/data-export`. ZIP with `leads.csv` + `campaigns.csv` + `messages.csv` + `audit_log.json`. `sanitize_csv_cell` + `csv.QUOTE_MINIMAL`. Rate-limit **1/day, peer-IP-keyed (`get_remote_address`, NOT XFF)** — closes XFF-rotation bypass on direct backend. 17-test pin.
- **Article 17 erasure** at `DELETE /operator/account`. **Three-factor gate**: X-API-Key + X-Admin-Token + JSON `Literal["DELETE MY ACCOUNT"]`. **Audit-first invariant**: row written to `account_deletions` BEFORE any DELETE; audit-write failure → 503 + skip destructive. FK order: campaign_messages → campaigns → orchestration_jobs → leads. Sentinel-UUID `delete().neq("id", _NEVER_UUID)`. Rate-limit 1/hour peer-IP. 16-test pin.
- `account_deletions` audit table: RLS deny-all (RESTRICTIVE). **30-day retention** via `src/scripts/purge_expired_audit_log.py` (wired in `security.yml`).

### Test inventory pointers
Per-defense file-by-file inventory: [`docs/security/test-inventory.md`](docs/security/test-inventory.md). AI quality & safety suite: [`docs/ai-test-suite.md`](docs/ai-test-suite.md). **6 critical pinned findings**:
1. `seo_score` is NOT input to `calculate_outreach_score`.
2. `segment_lead` pure regex, not Gemini.
3. `_get_strategic_insights` SELECTs only `name,company_name,audit_status,seo_score,lead_source` + separate ground-truth count (PR #245).
4. `discovery_search`/`run_massive_pipeline` schemas don't declare `limit`.
5. `verify_api_key` returns 403, not 401.
6. Discovery + SEO audit are NOT Gemini — excluded from cost budget.

**Parallel-terminal QA harness** (2026-05-28, PR #385/#386/#387): `test-results/_schema.md` pins `| ID | Category | Target | Test | Status | Detail |`. 6 terminals SEC/RESP/NAV/COMP/A11Y/API. `scripts/aggregate_test_results.py` rolls `test-results/NN-<slug>.md` into `TEST_RESULTS.md` + `_summary.json`. Inventory: 7 routes / 11 components / 42 backend endpoints. Auth-mint recipe at `test-results/_auth_method.md` (gitignored) — Supabase admin `generate_link` → `/auth/v1/verify` 303 → fragment-parse → `@supabase/ssr` cookie. 1h operator session; single-tenant invariant preserved (auth.users count 1 pre + post).

### CI/CD architecture pointer
15 workflows under `.github/workflows/`. Full: [`docs/ci-architecture.md`](docs/ci-architecture.md). Every action SHA-pinned with `# vX.Y.Z` (Dependabot atomic bumps). PR gate `ci.yml` (~20 checks). Post-merge `security.yml` (push + daily). Tagged-release: `deploy-backend.yml` + `release.yml` (tag `v*`) → GHCR → SLSA3 → cosign → Render API rollout. `workflow-pin-guard` rejects `@vN`. **pip-tools** + `--require-hashes` Dockerfile + `lockfile-sync` CI. Local-CI parity via pre-commit (`make install-hooks`). Semgrep direct-install. **Secret inventory + rotation** at [`docs/secret-inventory.md`](docs/secret-inventory.md): 29 secrets. Monthly: `SUPABASE_SERVICE_ROLE_KEY`, `RENDER_API_KEY`, `SUPABASE_DATABASE_URL`. Quarterly: `API_SECRET_KEY`, `ADMIN_TOKEN`, `GEMINI_API_KEY`.

## Performance + observability invariants

Full: [`docs/architecture/performance.md`](docs/architecture/performance.md). Load-bearing one-liners:
- **Cursor pagination `/leads`**: `?limit=1..200` + `?cursor=<base64url(json({c,k}))>`. Decoder fail-closed → page-1. ≤512 bytes raw, k ≤128 + `_CURSOR_KEY_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")` charset gate (k interpolates raw into `.or_()` at `src/utils/supabase_helper.py:159-161`; `,/)/(` would escape tie-break). `list_leads_recent`: `created_at.lt.<c>` OR `and(created_at.eq.<c>, unique_key.lt.<k>)`. Index `idx_leads_created_at_desc`.
- **Cold-start lazy imports**: 1.14s → 219ms via PEP 562. **Lifespan still blocks** on `db.check_schema()` + `orchestrator.recover_interrupted_jobs()` before uvicorn binds — move recovery to `asyncio.create_task` after `yield` to hit <5s on Render free (follow-up).
- **`/stats` cache** 60s TTL + `asyncio.Lock` double-checked, per-worker. Invalidated on `process_csv_background` upsert + `_process_in_chunks` `finally`. Single-lead mutations don't invalidate (lag ≤60s).
- **Streaming `/export/{download,outreach}`** use `StreamingResponse` + `_stream_leads_csv` paging 200 rows via keyset. ≈60 KB/chunk. Column order LOCKED via `_EXPORT_*_COLUMNS` tuples.
- **Structured JSON logging** envelope `{timestamp, level, logger, message, request_id, user_id, route, duration_ms?, exception?, <domain>...}`. `extra={…}` merges at top level (NOT nested under `"context"`). 11-test pin.
- **Request-context middleware** declared BEFORE `_block_logger` (Starlette LAST decorator = OUTERMOST wrapper). Honours valid `X-Request-ID` (`[A-Za-z0-9_-]{1,64}`); mints else. Binds ContextVars + Sentry per-request scope. Also stashes on `request.state` (survives BaseHTTPMiddleware task-hop — PR #246). **Does NOT clear in `finally`** — `StreamingResponse` body iterators run AFTER `call_next` returns; clearing would lose request_id.
- **Web-vitals RUM** `/metrics`: `WebVitalsMetric` Pydantic; `sendBeacon` with JSON `Blob` (bare beacon defaults `text/plain` → 422). Rate-limit 60/min. PR #242: `{reportAllChanges:true}` on `onCLS+onLCP`.
- **Block-logger middleware**: `WARN slow handler` when elapsed ≥ `SLOW_HANDLER_THRESHOLD_MS` (100 default). `extra={method, path, duration_ms, threshold_ms}`.
- **Load-test scaffolding** `tests/loadtest/`: `locustfile.py`, `bench_enrich.py`, `spike.sh`, `soak.sh` (24h + 8-signal `SOAK_PLAYBOOK.md`), `chaos.md` + `drop_supabase_pool.py` (local-only `CHAOS_LOCAL_ONLY=1`). VUs inject synthetic RFC1918 XFF.

## Sentry + Discord
Full wiring: [`docs/architecture/sentry-discord.md`](docs/architecture/sentry-discord.md) + [`docs/observability.md`](docs/observability.md) + [`docs/alerting.md`](docs/alerting.md). Load-bearing:
- **Sentry backend** init at module load; `sample_rate=1.0, traces_sample_rate=0.1, send_default_pii=False`. `before_send=_scrub_sensitive` strips auth headers + drops `/upload` body (CSV is lead PII). Skipped without `SENTRY_DSN`.
- **Sentry frontend** `@sentry/nextjs` canonical: `instrumentation.ts` → `sentry.{server,edge}.config.ts`; `instrumentation-client.ts`. Source maps `deleteSourcemapsAfterUpload: true`. Release tag = git SHA. Browser SDK **dynamic-imported inside `requestIdleCallback`** post-FCP (PR #419 `a55149b3`, 2026-05-29) — 142 KB gz SDK chunk off `rootMainFiles`, prod LCP 1502 → 1336 ms median (−11 %). Synchronous wrapper export of `onRouterTransitionStart` (no-op until SDK rebinds) satisfies Next 16's framework hook contract. **Tradeoff**: errors thrown in the ~50–200 ms before idle fire are NOT captured. Full pattern + measured deltas: `docs/observability.md` §1c.
- **`/monitoring` tunnel** has **manual fallback at `frontend/app/monitoring/route.ts`** (edge runtime) — Sentry webpack-plugin virtual route returned 404 in prod (RESP-044). Physical file beats virtual in Next 16 App Router. `Sentry.init({ tunnel: '/monitoring' })` pinned in `instrumentation-client.ts`. Handler **delegates to `@sentry/core`'s `handleTunnelRequest`** (PR #413, `4366ece9`, 2026-05-29): `Content-Length` fast-path 413, DSN-missing 204, anti-SSRF DSN allowlist match (403 on mismatch — gated by envelope-header `dsn` field, NOT incoming `Content-Type`), OPTIONS 204. **Do NOT add a Content-Type allowlist** — Sentry's tunnel transport uses `text/plain;charset=UTF-8` to skip CORS preflight; the old strict allowlist silently dropped every envelope for ~1 day. Verified end-to-end 2026-05-29T14:47Z by Sentry-EU ingest accepting event_id `5ed56e453b16…` from a browser-triggered Error. DSNs live: backend project_id `4511473178574928`, frontend `4511473196925008`, org `o4511473167695873`, EU region.
- **Discord 5 signals** via composite action `.github/actions/discord-notify/action.yml` (curl+jq+bash, no third-party). Single secret `DISCORD_WEBHOOK_URL`.

## Documentation map
- **Runbooks**: `docs/runbooks/{operator-guide,incidents,rollback,dispatch-cron,apply-phase-14-15-migrations,render-env-push}.md`. Incidents at `docs/runbooks/incidents/YYYY-MM-DD-<slug>.md`.
- **Onboarding**: `docs/onboarding.md`. **Observability**: `docs/observability.md`. **Alerting**: `docs/alerting.md`. **Launch**: `docs/launch-checklist.md`. **Support**: `docs/{support-process,faq}.md`. **Status**: `docs/status-page-setup.md`. **Roadmap**: `docs/roadmap.md`. **Legal**: `docs/legal/{privacy-policy,terms}.md` ⚠️ lawyer-review.
- **ADRs**: `docs/adr/{001..007}.md` (single-tenant, FastAPI, PostgREST not direct PG, Playwright/aiohttp, no soft delete, Gemini, Render not Vercel).
- **Inventories**: `docs/{secret-inventory,ci-architecture}.md`. **Architecture detail**: `docs/architecture/{backend-modules,frontend,performance,sentry-discord,ai-router,discovery-engine,session-archive}.md`.
- **Deep technical**: [`docs/api-security-invariants.md`](docs/api-security-invariants.md), [`docs/db-invariants.md`](docs/db-invariants.md), [`docs/ai-test-suite.md`](docs/ai-test-suite.md), [`docs/security/test-inventory.md`](docs/security/test-inventory.md), [`docs/perf/reports-2026-05-22.md`](docs/perf/reports-2026-05-22.md), [`docs/e2e-and-frontend-contracts.md`](docs/e2e-and-frontend-contracts.md), [`docs/bookbed-crossover.md`](docs/bookbed-crossover.md).

`README.md` at repo root is the single breadcrumb.

## AI Router + Discovery engine
Full invariants: [`docs/architecture/ai-router.md`](docs/architecture/ai-router.md) + [`docs/architecture/discovery-engine.md`](docs/architecture/discovery-engine.md). Most-load-bearing:
- `route_instruction()` attaches `lead_index` (unique_key + name + company_name, ≤200 rows) to Gemini contents — without context, model bails "data insufficient" on per-lead prompts.
- `/execute` rejects extra fields (`extra='forbid'`). `/ask` plan includes `reasoning`; frontend strips it before POST (`handleExecutePlan` builds `{task, params}` only) — else every Confirm 422s.
- `_get_strategic_insights()` prompt-body change ⇒ `tests/test_prompt_snapshots.py` fails until SHA256 regen via `UPDATE_PROMPT_SNAPSHOTS=1`.
- `_generate_outreach_draft()` Subject parsed via **atomic-group regex** `^(?>\s*)Subject(?>[ \t]*):(?>[ \t]*)([^\r\n]*)\r?\n` — fixed O(n²) ReDoS. Pinned by `tests/test_redos.py::TestSubjectParserReDoSRegression`.
- Discovery `find_leads(query, location)` — Google-Maps host hardcoded `google.com`, `query` `quote_plus`-encoded (no host-SSRF). Playwright route guard re-runs `assert_safe_url` on subresources + redirects (closes TOCTOU + redirect-chain).

## Frontend
Full files + conventions + cross-page nav + handler robustness: [`docs/architecture/frontend.md`](docs/architecture/frontend.md). Hard rules:
- **Next 16 `<Suspense>` wrap REQUIRED** on `app/page.tsx` because it's `'use client'` + uses `useSearchParams()`. Default export = `<Suspense fallback={null}><DashboardInner /></Suspense>`. Removing ⇒ `missing-suspense-with-csr-bailout` hard deploy blocker.
- Tokens from `globals.css` — never hardcode colors/rgba. Solid surface scale `--surface-base/-subtle/-elevated/-muted/-hover`. Single brand hue indigo `hsl(234,89%,64%)` via `--primary-hsl`.
- Breakpoints: `<1024` mobile drawer + `mobile-header` + hamburger; `1024-1280` icon-only desktop sidebar (80px); `>1280` full sidebar (280px). `.header-actions` wraps via `flex-wrap: wrap + row-gap` (NEVER `overflow-x: auto` — parent `overflow: hidden` clips). Mobile sidebar `transform: translateX()` NEVER `left:`. `prefers-reduced-motion: reduce` honored.
- Modals: `role="dialog" + aria-modal="true" + aria-labelledby + ESC handler`. **Backdrop scrolls when content > viewport**: `.modal-backdrop` = `align-items: flex-start + overflow-y: auto + padding: clamp(1rem,4vh,4rem) 1rem`. Do NOT inline `style={{padding}}` on `.modal-backdrop` — overrides scroll-pad (RESP-006).
- State-changing handlers hitting `/api/proxy/*` MUST: check `res.ok` → toast on fail; try/catch network toast; `aria-busy + disabled` during inflight, reset in `finally` (else rapid clicks duplicate Gemini calls = real money); destructive ops (`processAll, startMassivePipeline, handleDeepHuntAll, handleClearLeads`) gate with `confirm()` naming count + cost warning.
- Pydantic 422 shape `{detail: [{type, loc, msg, input, ctx}]}`. `AIChat.handleSubmit` joins `detail[].msg` so user sees "String should have at most 4000 characters" not generic placeholder.
- Cross-page nav: Dashboard owns modal + view-filter state. Non-dashboard pages navigate to `/?openSettings=1|openDiscovery=1|view=audited|high-risk|search=<term>` (bridge → `?q=`). Sidebar setters on non-dashboard MUST respect `(open)` arg: `(open) => { if (open) router.push('/?openSettings=1') }` — else `setShowDiscoveryModal(false)` opens wrong modal.
- No `any` in TS. No gradient text / `linear-gradient` on UI chrome / `backdrop-filter` blur (mobile drawer overlay only).

## Design Skills (Impeccable)
`npx skills add pbakaus/impeccable`. Commands: `/polish /audit /animate /bolder /quieter /distill /critique /colorize /harden /delight /clarify /adapt /onboard /normalize /extract /teach-impeccable /optimize /overdrive /arrange /typeset /frontend-design`.

## Cross-repo strategy (BookBed.io)

LDS is internal tooling (`OPERATOR_EMAIL` single-tenancy — [ADR-001](docs/adr/001-single-tenant-by-design.md)). Commercial SaaS in sibling `~/git/` repos: `bookbed-website/` (Next.js 16 marketing on Firebase App Hosting — **ahead of LDS** on `object-src/base-uri/form-action/COOP/CORP/XPCDP`) + `bookbed/` (Flutter SaaS + Firebase CF + Firestore + Stripe LIVE + Resend + `firebase_ai` Gemini chat — the real revenue surface).

Gap-analysis + phased action plan: [`docs/bookbed-crossover.md`](docs/bookbed-crossover.md). Phases A (bookbed-website CI hardening) → B (bookbed CF email CRLF guards) → **C ✅ shipped 2026-05-23** ([rab_booking#460](https://github.com/DanLika/rab_booking/pull/460) — Flutter Gemini `<UNTRUSTED_DATA>` fence + 14-test corpus) → D (backport headers to LDS) → E (long tail).

**Phase 13 = dogfood-only** (decided 2026-05-22): 13.14 crossover doc ✅, 13.1 hr-HR i18n ✅, 13.3 demo seed ✅, 13.5 DKIM/SPF/DMARC, 13.4 email dispatch, 13.15 two-week dogfood. Commercial items (Stripe billing, multi-tenancy, signup) belong in BookBed repos — see `docs/roadmap.md` "Later > Commercialization track".

## Architecture patterns
Full notes: [`docs/sessions/2026-05-22-patterns.md`](docs/sessions/2026-05-22-patterns.md). Load-bearing one-liners:
- **Layered (handler → service → repository)** — `backend/main.py` routing+auth+Pydantic; `src/services/<domain>.py` business logic on typed primitives, raises typed errors; `src/repositories/<domain>.py` PostgREST I/O. Campaigns is reference (PR #192); webhook_events (PR #344).
- **Error hierarchy** (`src/errors.py`): `DomainError` → `NotFoundError`/`ValidationError`/`ConfigurationError`/`LeadError`/`EnrichmentError`/`AuditError`. Raise specific; `except Exception` only at outermost boundary; never echo `str(exc)`.
- **Logging idiom**: inside `except` use `logger.exception(msg, *args)`. Never `error(..., exc_info=True)` long form.
- **Quality ratchet** (`.github/workflows/quality-ratchet.yml`): 5 metrics vs `.quality-baselines.json`; never raise baseline to silence a finding.
- **Test org**: `tests/{unit,integration,e2e,security,quality}/`. Markers `@pytest.mark.{slow,live,security,integration,e2e}`. Directory + marker BOTH required. **Contract test** required per new producer↔verifier pair — see [audit](docs/audits/2026-05-26-contract-test-audit.md).
- **Constants modules**: `src/utils/constants.py` + `frontend/app/lib/constants.ts`. Parity invariant `MAX_UPLOAD_BYTES` == `MAX_PROXY_BODY_BYTES` (manual review).
- **Quality reports** — weekly Monday: 11 trackers under `tests/quality/`.

Known pre-existing failure: `tests/unit/test_logging_config.py::test_setup_logging` (test-ordering, defer).

## Phase 13 dogfood prep (i18n + email stack)
- **next-intl cookie-only (PR #249)** — locale via `NEXT_LOCALE` cookie; `withNextIntl(withSentryConfig(...))` plugin order; `layout.tsx` `force-dynamic`. **hr.json machine-quality** — needs native review. Full: [`docs/sessions/2026-05-23-dogfood-prep.md`](docs/sessions/2026-05-23-dogfood-prep.md).
- **Email stack (PR #243, no wiring)** — [`docs/email-deliverability.md`](docs/email-deliverability.md) + [`docs/email-dispatch-architecture.md`](docs/email-dispatch-architecture.md). Domain `mail.leaddatascraper.com`, Resend EU, DMARC ramp `none → quarantine → reject`. 5-PR sequence: ResendEmailSender (HTTP not SMTP), schema additions, `POST /webhooks/resend` Svix HMAC, Render Cron dispatcher (3/hr per-domain, 50/day, 09–18 Europe/Sarajevo), `/campaigns/{id}/send`. **Do NOT wire** until DNS green + mail-tester 10/10.

## context-mode — MANDATORY routing rules

Full rules: [`docs/runbooks/context-mode.md`](docs/runbooks/context-mode.md). Hard injunctions (these PROTECT your context window):
- **BLOCKED**: `curl`/`wget`/`WebFetch`/inline HTTP (`fetch('http`, `requests.get/post(`, `http.get/request(`). Use `ctx_fetch_and_index(url, source)` then `ctx_search(queries)`.
- **REDIRECTED**: Bash >20-line output (Bash OK only for `git`, `mkdir`, `rm`, `mv`, `cd`, `ls`, `npm install`, `pip install`). Use `ctx_batch_execute(commands, queries)` or `ctx_execute(language: "shell", code)`. Read-for-analysis (not Edit) → `ctx_execute_file(path, lang, code)`. Grep large → `ctx_execute(shell, grep)`.
- **Tool hierarchy**: (1) `ctx_batch_execute(commands, queries)` PRIMARY — one call replaces 30+. (2) `ctx_search(queries: [...])` follow-up, pass all questions as array. (3) `ctx_execute | ctx_execute_file` processing — only stdout enters context. (4) `ctx_fetch_and_index` then `ctx_search`. (5) `ctx_index(content, source)` FTS5 store.
- Subagent routing auto-injected. Bash-type subagents upgraded to general-purpose.
- Responses <500 words. Artifacts to FILES (return path + 1-liner). Indexing: descriptive source labels.
- `ctx stats` → `ctx_stats`. `ctx doctor` → `ctx_doctor` (run output as checklist). `ctx upgrade` → `ctx_upgrade` (run output).

## Session log archive
Full hook list: [`docs/architecture/session-archive.md`](docs/architecture/session-archive.md). Recent + load-bearing:
- 2026-05-22 patterns (#185–#199) — layered arch / errors / logging / ratchet / test-org.
- 2026-05-23 drain (#235–#251) — backend security headers, WebVitals, TOTAL LEADS, Insights, request.state, REVOKE, poller backoff.
- 2026-05-26 Phase 14+15 stack (21 PRs; chained-base + GH outage admin-merge), sweep (pytest 1064/0, useSyncExternalStore refactor), readiness (Render env pre-flight 5/7 missing, ruff F821 PEP-562 false-positive).
- 2026-05-27 schema-apply-smoke-fixes (Phase 14+15 schema via Mgmt API 5→11 tables; 1158/0 smoke; PR #353 ESLint + #354 proxy `/api/proxy/metrics` 401; `.env API_SECRET_KEY=` duplicate-line trap).

## End-to-end smoke flow (verified 2026-05-21)
Logged-in → AI chat → natural-language → Confirm & Execute → Playwright crawl → Supabase upsert. Verified via chrome-devtools MCP: `"How many leads?"` → `STATUS_CHECK` → `"<N> leads total."`; `"Find me 3 dentists in Mostar"` → `DISCOVERY_SEARCH` plan card → orchestrator → 8 leads in ~35s. Re-run via MCP if auth/proxy/orchestrator wiring changes.

## Operational gotchas (load-bearing)

- **Parallel-session contention**: multiple `claude --dangerously-skip-permissions` against one worktree race on HEAD. Mitigation: dedicated worktree (`git worktree add -b <new> ../<sibling> origin/main`); `git symbolic-ref HEAD` verify after `git checkout -b` AND before EACH write batch; atomic Bash heredoc for stage+commit+push when single-worktree.
- **macOS `pkill -f X -f Y` only honors LAST `-f`**. Use separate calls + verify: `pkill -f X; pkill -f Y; pgrep -f X || echo clean`.
- **chrome-devtools-mcp `.click()` can no-op silently** on stale build with no handler. Add `console.log` at handler entry FIRST + rebuild before concluding handler broken.
- **Render `x-render-routing: no-server` ≠ free-tier sleep** on `plan: starter` (no auto-suspend) → manual pause / deletion / billing / failed deploy. Check dashboard + status.render.com first.
- **Docs-PR stack via sequential rebase** when N>1 docs PRs append to same insertion point. `--force-with-lease=<branch>:<expected-tip>` not bare `--force`. Beyond stack-of-4 → combined PR.
- **`gh run view --log-failed` only surfaces step names** at job-setup-level failures. Open run page in UI when every job fails simultaneously (env-level: expired secret / broken `pip install` / runner config).
