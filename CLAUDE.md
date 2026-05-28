# LeadDataScraper

## Project Overview
Lead data scraping and enrichment pipeline with Supabase backend and Next.js dashboard frontend.

## Tech Stack
- **Backend**: Python, FastAPI, Supabase (database), Playwright, Google GenAI
- **Frontend**: Next.js (App Router), React 19, TypeScript, Recharts, Lucide icons

## Backend Architecture
- `backend/main.py` — FastAPI app. Lazy module-level singletons (`db`, `router`, `auditor`, `orchestrator`) via module `__getattr__` so heavy chains don't fire at import. **PEP 562 caveat**: `__getattr__` doesn't fire for bare-name `LOAD_GLOBAL` inside same-module functions. Lifespan attribute-accesses each name via `sys.modules[__name__]` to populate `globals()`. See "Cold-start lazy imports".
- `src/utils/supabase_helper.py` — Supabase wrapper (`SUPABASE_SERVICE_ROLE_KEY`). Hot-path reads `asyncio.to_thread`-wrapped.
- `src/utils/stats_cache.py` — 60s TTL + `asyncio.Lock` stampede guard. Per-worker singleton.
- `src/utils/query_profiler.py` — Dev-only, env-gated (`QUERY_PROFILER=1`). `assert_o1(per_unit=N)` for N+1 guards.
- `src/scrapers/seo_audit.py` — Async SEO auditor (aiohttp, no Playwright).
- `src/scrapers/discovery_engine.py` — Google Maps via Playwright.
- `src/scrapers/enrichment_engine.py` — Shared-Chromium pool, per-lead `new_context()`. `aclose()` MUST run on teardown.
- `src/core/task_orchestrator.py` — Background jobs. `_process_in_chunks` `finally` calls `enricher.aclose()` + `stats_cache.invalidate()`.
- `src/core/agentic_router.py` — AI instruction routing.

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
- **Control / format-char rejection in user-facing strings**: `safe_constr(...)` in `src/schemas/sanitized_str.py` is a drop-in for `pydantic.constr(...)` that ALSO rejects NUL + `unicodedata.category Cc/Cf` (except `\t \n \r`) via an `AfterValidator` raising `PydanticCustomError`. Plain `ValueError` puts the exception object in `ctx.error` → starlette JSON encoder 500s. Applied to all 20 user-facing `constr(...)` sites across 9 request models in `backend/main.py` (PipelineFilters / Campaign{Create,Update} / LeadProcessRequest / AskInstruction / DiscoveryRequest / PipelineRequest / ExecutePlanParams / WebVitalsMetric). Closes the `500 → 422` gap found by QA terminal-6 sweep on `POST /discovery/start` + `POST /campaigns` (`test-results/06-backend-api.md` ids API-127 + API-201). Pinned by `tests/security/test_control_char_rejection.py` (3-layer: unit + Pydantic-model + HTTP against real ASGI app).

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
- **16 DB invariant gates** in `src/scripts/` (run in `ci.yml` + `security.yml`): schema drift, referential integrity, hot-path indexes (5 probes), **17 CHECK constraints** (post-2026-05-27 hardening: `sequence_variants_body_size` body≤16384/subject≤998, `sequence_variants_content_type_allowed` ∈ text|html, `webhook_events_event_id_size` 1..256, `sequence_steps_window_ordered`, `sequence_steps_send_days_format` regex allowlist, `campaign_messages_bounce_reason_size` ≤200), JSONB shape, NULL audit, orphan + zombie sweep (1 auto-heal on `running > 4h`), concurrency tests (5 invariants incl. `pg_advisory_xact_lock`), per-role `statement_timeout` (anon 3s / authenticated 8s / service_role 30s), connection pool, DB bloat, slow query, grants matrix, function safety, ANALYZE freshness, JSONB GIN suggestions, storage size + WoW growth, deep PITR (disabled), migration safety (disabled). Catalog: [`docs/db-invariants.md`](docs/db-invariants.md).

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

### Frontend hardening
- Outreach modal `mailto:` href: `encodeURIComponent` lead email + subject + body.
- Dep pinning: `package.json` drops `^` on `next`, `@supabase/ssr`, `@supabase/supabase-js`. `postcss` override pinned `^8.5.10`.
- Login brute-force (`frontend/utils/loginThrottle.ts`): 5/60s per-IP. `MAX_BUCKETS=10_000` hard cap + oldest-eviction.
- Proxy `BACKEND_URL` scheme assertion: `_assertBackendSchemeAllowed` runs at request time (not module load — would crash `next build` against dev backend). Prod requires `https://` unless loopback (`127.0.0.1`, `localhost`, `*.localhost`).

### Test inventory pointers
Per-defense file-by-file inventory (offline + frontend-node + opt-in e2e + test-infra patterns): [`docs/security/test-inventory.md`](docs/security/test-inventory.md). AI quality & safety suite (offline + live tiers; ~15 test files): [`docs/ai-test-suite.md`](docs/ai-test-suite.md). **6 critical pinned findings**:
1. `seo_score` is NOT input to `calculate_outreach_score`.
2. `segment_lead` pure regex, not Gemini.
3. `_get_strategic_insights` SELECTs only `name,company_name,audit_status,seo_score,lead_source` + separate ground-truth count (PR #245).
4. `discovery_search`/`run_massive_pipeline` schemas don't declare `limit`.
5. `verify_api_key` returns 403, not 401.
6. Discovery + SEO audit are NOT Gemini — excluded from cost budget.

**Parallel-terminal QA harness** (2026-05-28, PR #385/#386/#387): `test-results/_schema.md` pins the shared `| ID | Category | Target | Test | Status | Detail |` table; six terminal prefixes (SEC/RESP/NAV/COMP/A11Y/API). `scripts/aggregate_test_results.py` rolls every `test-results/NN-<slug>.md` into `TEST_RESULTS.md` + `test-results/_summary.json` (PASS/FAIL/SKIP/BLOCKED, malformed status coerced to FAIL with parser warning). Inventory: 7 routes / 11 components / 42 backend endpoints / cold-start 0.30 s backend / 0.83 s frontend. **Terminal 6 (API) results** in `test-results/06-backend-api.md` — 252 atomic tests; 2 P3 findings (API-127 `/discovery/start` 500 + API-201 `/campaigns` 500 on adversarial-string input) **fixed in PR #387** via `safe_constr`. Auth-mint recipe at `test-results/_auth_method.md` (gitignored) — Supabase admin `generate_link` → `/auth/v1/verify` 303 → fragment-parse → `@supabase/ssr` cookie. Mints 1h operator session for UI terminals without password / new user / reset. Single-tenant invariant verified preserved (auth.users count 1 pre and post).

### CI/CD architecture pointer
15 workflows under `.github/workflows/`. Full: [`docs/ci-architecture.md`](docs/ci-architecture.md). Every action SHA-pinned with `# vX.Y.Z` comment (Dependabot bumps atomically). PR gate `ci.yml` (~20 checks). Post-merge `security.yml` (push + daily cron). Tagged-release: `deploy-backend.yml` (push main) + `release.yml` (tag `v*`) → GHCR → SLSA3 → cosign verify → Render API rollout. `workflow-pin-guard` rejects `@vN` tag refs. Trackers: flakiness-detector, mutation-test (80 % kill on ssrf_guard/prompt_safety/leadhunter), workflow-drift. **pip-tools** + `--require-hashes` Dockerfile + `lockfile-sync` CI. **Local-CI parity** via pre-commit (`make install-hooks`). Semgrep direct-install (deprecated `returntocorp/semgrep-action@v1` removed — org renamed).

**Secret inventory + rotation** at [`docs/secret-inventory.md`](docs/secret-inventory.md): 29 secrets. Monthly: `SUPABASE_SERVICE_ROLE_KEY`, `RENDER_API_KEY`, `SUPABASE_DATABASE_URL`. Quarterly: `API_SECRET_KEY`, `ADMIN_TOKEN`, `GEMINI_API_KEY`.

## Performance + observability invariants

- **Cursor pagination `/leads`**: `?limit=1..200` + `?cursor=<base64url(json({c,k}))>`. Decoder fail-closed → page-1. ≤512 bytes raw, k ≤128 + **`_CURSOR_KEY_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")` charset gate** (k interpolates raw into `.or_()` predicate at `src/utils/supabase_helper.py:159-161`; `,`/`)`/`(` would escape the tie-break clause). `list_leads_recent` uses `created_at.lt.<c>` OR `and(created_at.eq.<c>, unique_key.lt.<k>)` tie-break. Index: `idx_leads_created_at_desc`.
- **Async DB wrappers** in `SupabaseHelper` `to_thread`-wrap sync supabase-py `.execute()` (only `/leads`, `/stats`, `/process-lead`, `/process-all`; background stays sync).
- **`/stats` cache** 60s TTL + `asyncio.Lock` double-checked, per-worker. Invalidated on `process_csv_background` upsert + `_process_in_chunks` `finally`. Single-lead mutations don't invalidate (lag ≤60s).
- **Cold-start lazy imports.** Module `__getattr__` resolves `db`/`router`/`auditor`/`orchestrator` on attribute access. 1.14s → 219ms. **PEP 562 trap**: doesn't fire for bare-name `LOAD_GLOBAL` inside same-module functions. Lifespan runs a priming loop walking `sys.modules[__name__]` per-name with try/except (missing env disables that singleton only). Any future lazy singleton MUST land in the loop.
- **Lifespan still blocks cold start**: `db.check_schema()` + `orchestrator.recover_interrupted_jobs()` before uvicorn binds. Move recovery into `asyncio.create_task` after `yield` to hit <5s on Render free. Follow-up.
- **Block-logger middleware**: `WARN slow handler` when elapsed ≥ `SLOW_HANDLER_THRESHOLD_MS` (100 default). Structured `extra={method, path, duration_ms, threshold_ms}`.
- **Web-vitals RUM** at `/metrics`: `WebVitalsMetric` Pydantic; `sendBeacon` with JSON `Blob` (bare beacon defaults `text/plain` → 422). Rate-limit 60/min. PR #242: `{reportAllChanges:true}` on `onCLS`+`onLCP`.
- **Streaming `/export/{download,outreach}`** use `StreamingResponse` + `_stream_leads_csv` paging 200 rows via keyset. ≈60 KB/chunk. Column order LOCKED via `_EXPORT_*_COLUMNS` tuples.
- **Query profiler** refuses without `QUERY_PROFILER=1`. `assert_o1(per_unit=N, tolerance=2.0)`. Static audit 2026-05-22: 0 N+1.
- **EnrichmentEngine shared-browser pool**: one Chromium / instance; per-lead `new_context()`. `aclose()` MUST run on teardown.
- **Load-test scaffolding** `tests/loadtest/`: `locustfile.py`, `bench_enrich.py`, `spike.sh`, `soak.sh` (24h + 8-signal `SOAK_PLAYBOOK.md`), `chaos.md` + `drop_supabase_pool.py` (local-only `CHAOS_LOCAL_ONLY=1`). VUs inject synthetic RFC1918 XFF.
- **Structured JSON logging**: envelope `{timestamp, level, logger, message, request_id, user_id, route, duration_ms?, exception?, <domain>...}`. `extra={…}` merges at top level (NOT nested under `"context"`). `JsonFormatter` + `_CRLFScrubFilter` cooperate. 11-test pin.
- **Request-context middleware** declared BEFORE `_block_logger` (Starlette LAST decorator = OUTERMOST wrapper). Honours valid `X-Request-ID` (`[A-Za-z0-9_-]{1,64}`), mints `uuid.uuid4().hex` else. Binds ContextVars + Sentry per-request scope. Also stashes on `request.state` (survives BaseHTTPMiddleware task-hop — PR #246). `_block_logger` reads `request.state` into `extra={…}`; `JsonFormatter` merges extras BEFORE ContextVar `setdefault`. **Does NOT clear in `finally`** — `StreamingResponse` body iterators run AFTER `call_next` returns; clearing would lose request_id.

## Observability + Alerting pointers

Full wiring: [`docs/observability.md`](docs/observability.md) + [`docs/alerting.md`](docs/alerting.md).

- **Sentry backend** init at module load in `backend/main.py`. `sample_rate=1.0`, `traces_sample_rate=0.1`, `send_default_pii=False`. Skipped without `SENTRY_DSN`. `before_send=_scrub_sensitive` strips auth headers + drops `/upload` body entirely (CSV is lead PII).
- **Sentry frontend** uses `@sentry/nextjs` canonical layout: `instrumentation.ts` (server) → `sentry.{server,edge}.config.ts`; `instrumentation-client.ts` (browser). `withSentryConfig` uploads source maps at build with `deleteSourcemapsAfterUpload: true`.
- **Release tag = git SHA**: backend `Dockerfile ARG GIT_SHA`; frontend `NEXT_PUBLIC_SENTRY_RELEASE → SENTRY_RELEASE → RENDER_GIT_COMMIT → "unknown"`.
- **`/_sentry/test`** gated by `SENTRY_TEST_ENABLED=1`. **Tunnel `/monitoring`** in `withSentryConfig` bypasses ad-blockers (added to middleware public allowlist). Per-request scope tag `request_id` + (if known) `user.email`.
- **Discord 5 signals → one channel** via composite action `.github/actions/discord-notify/action.yml` (curl+jq+bash, no third-party action). Signals: synthetic-monitor (3 consec fail of 4 checks), storage-monitor (70/90% bands via grep on `HARD threshold`/`crossing soft threshold`), mutation-test (kill rate), cold-start-monitor (daily 04:00 UTC, >30s OR non-2xx), cert-expiry-monitor (weekly Mon 09:00, <30 days OR unreachable). **`cost-report.yml`** weekly Mon 08:00 (Gemini approximate). Single secret `DISCORD_WEBHOOK_URL`; optional `PROD_FRONTEND_HOST`/`PROD_BACKEND_HOST`/`PROD_BACKEND_URL`.

## Documentation map (operator-facing — full content in `docs/`)

- **Runbooks**: `docs/runbooks/{operator-guide,incidents,rollback}.md`. Incidents at `docs/runbooks/incidents/YYYY-MM-DD-<slug>.md`.
- **Onboarding**: `docs/onboarding.md`. **Observability**: `docs/observability.md`. **Alerting**: `docs/alerting.md`. **Launch**: `docs/launch-checklist.md`. **Support**: `docs/{support-process,faq}.md`. **Status**: `docs/status-page-setup.md`. **Roadmap**: `docs/roadmap.md`. **Legal**: `docs/legal/{privacy-policy,terms}.md` ⚠️ lawyer-review.
- **ADRs**: `docs/adr/{001..007}.md` (single-tenant, FastAPI, PostgREST not direct PG, Playwright/aiohttp, no soft delete, Gemini, Render not Vercel).
- **Inventories**: `docs/{secret-inventory,ci-architecture}.md`.
- **Deep technical**: [`docs/api-security-invariants.md`](docs/api-security-invariants.md), [`docs/db-invariants.md`](docs/db-invariants.md), [`docs/ai-test-suite.md`](docs/ai-test-suite.md), [`docs/security/test-inventory.md`](docs/security/test-inventory.md), [`docs/perf/reports-2026-05-22.md`](docs/perf/reports-2026-05-22.md), [`docs/e2e-and-frontend-contracts.md`](docs/e2e-and-frontend-contracts.md), [`docs/bookbed-crossover.md`](docs/bookbed-crossover.md).
- **Sessions**: see "Session log archive" below.

`README.md` at repo root is the single breadcrumb.

## AI Router invariants (`src/core/agentic_router.py`)
- `route_instruction()` attaches `lead_index` (unique_key + name + company_name, ≤200 rows) to Gemini contents so model can resolve "Audit Alpha Tech" → `seo_audit(unique_key=...)`. Without context, model bails "data insufficient" on every per-lead prompt.
- `_execute_database_query()` selects `unique_key, name, company_name, audit_status, seo_score, lead_source, email, phone, website, high_risk_flag, segment`. Query prompt embeds definitions ("high risk" = `high_risk_flag` true OR `seo_score<50` OR `audit_status=='Failed'`; "healthy" = Completed + score≥70 + not high-risk) so answers match UI filter semantics.
- `/ask` auto-executes `DATABASE_QUERY`/`STATUS_CHECK`/`GET_INSIGHTS` (read-only) and surfaces `result.answer/message/formatted-insights/summary`. `task=="UNKNOWN"` surfaces `plan.raw` (small-talk) instead of a confusing plan card.
- `/execute` rejects extra fields (`extra='forbid'`). `/ask` plan includes `reasoning`; frontend strips it before POST (`handleExecutePlan` builds `{task, params}` only) — without strip every Confirm 422s.
- `_get_status_summary()` returns one-line summary as both `answer` + `summary`.
- `_get_strategic_insights()` (PR #245) fetches DB-wide count via separate `select("unique_key", count="exact").limit(1)` (one scalar — keeps finding #3 intact) and embeds `GROUND TRUTH` block. **CI side-effect**: changes prompt body → `tests/test_prompt_snapshots.py` fails until SHA256 regenerated via `UPDATE_PROMPT_SNAPSHOTS=1`.
- `_generate_outreach_draft()` returns `{draft, subject, lead_name, lead_email, operator_name}`. Subject parsed via **atomic-group regex** `^(?>\s*)Subject(?>[ \t]*):(?>[ \t]*)([^\r\n]*)\r?\n` — previous form was O(n²) ReDoS, fixed. `OPERATOR_NAME` env defaults "Your Name". Pinned: `tests/test_redos.py::TestSubjectParserReDoSRegression`.

## Discovery engine invariants (`src/scrapers/discovery_engine.py`)
- `find_leads(query, location)` — Google-Maps. Host hardcoded `google.com`, `query` `quote_plus`-encoded (no host-SSRF). Playwright route guard re-runs `assert_safe_url` on subresources + redirects (closes TOCTOU + redirect-chain hops).
- `unique_key` from `!1s<id>!` segment of place URL (stable); fallback 16-char MD5 of `name` (`usedforsecurity=False`).
- `_extract_lead_data` returns `{name, unique_key, website, phone, rating, audit_status, lead_source: 'google_maps', address}`. Address via `_extract_address`: `button[data-item-id='address']` → `button[aria-label^='Address:']` → `[data-tooltip='Copy address']`. Opens side-panel if closed. Normalised via `re.sub(r'\s+', ' ', ...)` + `re.search(r'[\w].*')`. Returns `None` on miss.

## Next 16 prerender + `useSearchParams` contract
- `app/page.tsx` is `'use client'` + uses `useSearchParams()`. Next 16 requires `<Suspense>` wrap so `next build` can prerender without CSR bailout. Default export = `<Suspense fallback={null}><DashboardInner /></Suspense>`. Removing → `missing-suspense-with-csr-bailout` hard deploy blocker on Render `npm run build`.
- Local dev uvicorn ships `server: uvicorn`; Dockerfile CMD adds `--no-server-header`. Next.js proxy strips upstream `server` (belt-and-braces).

## End-to-end smoke flow (verified 2026-05-21)
Logged-in → AI chat → natural-language → Confirm & Execute → Playwright crawl → Supabase upsert. Verified via chrome-devtools MCP: `"How many leads?"` → `STATUS_CHECK` → `"<N> leads total."`; `"Find me 3 dentists in Mostar"` → `DISCOVERY_SEARCH` plan card → orchestrator → 8 leads in ~35s. Re-run via MCP if auth/proxy/orchestrator wiring changes.

## Live perf-test reports — 2026-05-22 sweep
6-report sweep against `npm run start` prod build (`fix/csp-nonce-rsc-hydration`). 119.9 FPS scroll, INP 101 ms, CLS 0.00. Bugs flagged: AI insights non-AbortSignal `signal` (P1), orchestrator poller no visibility-pause (FIXED PR #233), Inter silent fallback (FIXED PR #239), Login UX missing spinner + throttle toast, favicon revalidate tax. Phase 9.10 full pipeline live shipped 2026-05-23 (PR #274): 19/21 Completed, 2 Failed, 5 drafts, Gemini ~$0.037/287k tokens. 4 atomic `drain` fix PRs (#275–#278). Skipped: 9.6 Coverage, 9.8 Live CSP/HSTS, 9.12 Visual smoke. Full reports: [`docs/perf/reports-2026-05-22.md`](docs/perf/reports-2026-05-22.md).

## Cross-page navigation contract
Dashboard owns modal + view-filter state; non-dashboard pages navigate to `/` with query params, dashboard consumes-then-strips: `/?openSettings=1`, `/?openDiscovery=1`, `/?view=audited|high-risk`, `/?search=<term>` (bridge translates to `?q=` on consume). Setters passed to Sidebar on non-dashboard pages MUST respect the `(open)` arg: `(open) => { if (open) router.push('/?openSettings=1') }` — else Sidebar's `setShowDiscoveryModal(false)` would navigate to `/?openDiscovery=1` and open wrong modal.

## E2E test suite + frontend contracts
[`docs/e2e-and-frontend-contracts.md`](docs/e2e-and-frontend-contracts.md) — filter ↔ URL vocab (`?segment/?status/?min/?q/?sort`), `apiFetch` 401 + offline-queue, `GET /orchestrator/active`, drag-drop ingest, 18 spec files (chromium/firefox/webkit/iphone-14/pixel-7), cooperative-cancel pytest, ops scripts (schema-migration-smoke, auth-smoke, contract-smoke, preview-smoke, data-integrity-cron).

## Frontend handler robustness pattern
Every state-changing handler hitting `/api/proxy/*` MUST: (1) check `res.ok` → surface `data.detail || data.error || \`<Action> failed (HTTP ${status})\`` via `showToast(..., 'error')`; (2) try/catch with network-failure toast; (3) `aria-busy` + `disabled` on trigger during inflight, reset in `finally` (rapid clicks otherwise fire duplicate Gemini calls — cost real money); (4) destructive ops (`processAll`, `startMassivePipeline`, `handleDeepHuntAll`, `handleClearLeads`) gate with `confirm()` naming count + one-line cost warning.

Pydantic 422 = `{detail: [{type, loc, msg, input, ctx}]}`. `AIChat.handleSubmit` joins `detail[].msg` so user sees "String should have at most 4000 characters" not generic placeholder.

## Frontend Architecture
- `app/page.tsx` — Dashboard. Cursor-pagination state + `loadMoreLeads`. Heavy children lazy via `next/dynamic`: `HealthChart` (recharts), `AIChat`, `LeadTable`. `StatsCards` accepts `totalLeads` from `/stats.total_leads` (PR #244 — was showing page-load 50 while DB held 521); falls back to `leads.length` until first /stats. **Outstanding**: PENDING/HIGH-RISK/HEALTHY still derive from loaded slice — needs per-bucket counts in `/stats`.
- `app/insights/page.tsx` — Recharts panels extracted to `InsightsCharts` (lazy). Hits `/leads?limit=200` for aggregation.
- `app/campaigns/page.tsx` — Outreach campaigns.
- `app/components/LeadTable.tsx` — Virtualized. `@tanstack/react-virtual`, CSS-grid rows (not `<table>` — virtualizer needs absolute positioning), sticky header, variable heights via `measureElement`, 20-row overscan. Owns "Load more" + auxiliary panel + `cleanMarkdown` + `CollapsibleText`.
- `app/components/InsightsCharts.tsx` — PieChart + BarChart extracted from `/insights` so recharts (~80 KB gz) loads via lazy chunk.
- `app/components/WebVitalsReporter.tsx` — `useEffect` registers CLS/INP/LCP/FCP/TTFB; `sendBeacon` to `/api/proxy/metrics`. Renders nothing.
- Other components: `AIChat.tsx`, `Sidebar.tsx`, `HealthChart.tsx`, `StatsCards.tsx`, `FilterBar.tsx`, `LocaleSwitcher.tsx`.
- `app/types/lead.ts` — Shared `Lead` interface (imported by `page.tsx` + `LeadTable.tsx` — two identical interfaces in different files break callback variance).
- `app/globals.css` — Design tokens. `--font-main` no longer includes Inter (PR #239).
- `utils/apiConfig.ts` — `apiFetch()` wrapper.

## Frontend Conventions
- CSS design tokens from `globals.css` — never hardcode colors / rgba.
- Surface scale (solid): `--surface-base` < `--surface-subtle` < `--surface-elevated` < `--surface-muted` < `--surface-hover`. Cards: `--card-bg` + `--border-subtle` + `--card-shadow` (no backdrop-filter).
- Tints: `--primary-tint-{5,10,15,20}`, `--success-tint`, `--warning-tint`, `--error-tint`, `--linkedin-tint`. Single brand hue indigo `hsl(234,89%,64%)` via `--primary-hsl`. Secondary/accent reserved for charts.
- Theme: dark default + `@media (prefers-color-scheme: light)` + `[data-theme="light"]` override. Modal backdrop: `.modal-backdrop` (driven by `--modal-backdrop-bg`).
- Glass tokens (`--glass-*`) are legacy aliases mapped to solid surfaces — prefer solid names.
- 44px min touch target (`--touch-target-min`). Z-index: sidebar=100, mobile-backdrop=199, mobile-sidebar=200, chat=400, modals=500.
- Modals: `role="dialog"` + `aria-modal="true"` + `aria-labelledby` + ESC handler. Icon-only buttons need `aria-label`.
- No `any` in TS. No gradient text / `linear-gradient` on UI chrome / `backdrop-filter` blur (mobile drawer overlay only). Mobile sidebar via `transform: translateX()`, never `left:`. `prefers-reduced-motion: reduce` honored globally.

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
- **Quality ratchet** (`.github/workflows/quality-ratchet.yml`): 5 metrics vs `.quality-baselines.json`; never raise baseline to silence a finding. **Exception block** (`_update_policy_exception_<date>` under `_meta`) is the documented escape hatch when admin-merge has already bypassed the ratchet — used in PR #373 (2026-05-28) to refresh ruff 90→277 / mypy_strict 401→637 / pylint_score 10.0→8.74 after the Phase 14/15 stack absorbed the drift; see [[quality-baseline-drift-exception-template]] memory for the template.
- **Test org**: `tests/{unit,integration,e2e,security,quality}/`. Markers `@pytest.mark.{slow,live,security,integration,e2e}`. Directory + marker BOTH required. **Contract test** required per new producer↔verifier pair — see [audit](docs/audits/2026-05-26-contract-test-audit.md).
- **Constants modules**: `src/utils/constants.py` + `frontend/app/lib/constants.ts`. Parity invariant `MAX_UPLOAD_BYTES` == `MAX_PROXY_BODY_BYTES` (manual review).
- **Quality reports** — weekly Monday: 11 trackers under `tests/quality/`.

Known pre-existing failure: `tests/unit/test_logging_config.py::test_setup_logging` (test-ordering, defer).

## Phase 13 dogfood prep (i18n + email stack)
- **next-intl cookie-only (PR #249)** — locale via `NEXT_LOCALE` cookie; `withNextIntl(withSentryConfig(...))` plugin order; `layout.tsx` `force-dynamic`. **hr.json machine-quality** — needs native review. Full: [`docs/sessions/2026-05-23-dogfood-prep.md`](docs/sessions/2026-05-23-dogfood-prep.md).
- **Email stack (PR #243, no wiring)** — [`docs/email-deliverability.md`](docs/email-deliverability.md) + [`docs/email-dispatch-architecture.md`](docs/email-dispatch-architecture.md). Domain `mail.leaddatascraper.com`, Resend EU, DMARC ramp `none → quarantine → reject`. 5-PR sequence: ResendEmailSender (HTTP not SMTP), schema additions, `POST /webhooks/resend` Svix HMAC, Render Cron dispatcher (3/hr per-domain, 50/day, 09–18 Europe/Sarajevo), `/campaigns/{id}/send`. **Do NOT wire** until DNS green + mail-tester 10/10.

## context-mode — MANDATORY routing rules

You have context-mode MCP tools available. These rules are NOT optional — they protect your context window from flooding. A single unrouted command can dump 56 KB into context and waste the entire session.

### BLOCKED commands — do NOT attempt these
- **curl / wget** — intercepted and replaced with error. Use `ctx_fetch_and_index(url, source)` or `ctx_execute(language: "javascript", code: "const r = await fetch(...)")`.
- **Inline HTTP** (`fetch('http`, `requests.get(`, `requests.post(`, `http.get(`, `http.request(`) — intercepted. Use `ctx_execute(language, code)`.
- **WebFetch** — denied entirely. URL extracted; use `ctx_fetch_and_index` then `ctx_search(queries)`.

### REDIRECTED tools — use sandbox equivalents
- **Bash >20 lines output** — Bash is ONLY for `git`, `mkdir`, `rm`, `mv`, `cd`, `ls`, `npm install`, `pip install`, and other short-output commands. For everything else use `ctx_batch_execute(commands, queries)` or `ctx_execute(language: "shell", code: "...")`.
- **Read (for analysis)** — if reading to Edit, Read is correct. If reading to analyze/explore/summarize, use `ctx_execute_file(path, language, code)` — only your printed summary enters context.
- **Grep (large results)** — use `ctx_execute(language: "shell", code: "grep ...")`.

### Tool selection hierarchy
1. **GATHER**: `ctx_batch_execute(commands, queries)` — primary tool. Runs all commands, auto-indexes output, returns search results. ONE call replaces 30+ individual calls.
2. **FOLLOW-UP**: `ctx_search(queries: ["q1", "q2", ...])` — query indexed content. Pass ALL questions as array in ONE call.
3. **PROCESSING**: `ctx_execute(language, code)` | `ctx_execute_file(path, language, code)` — sandbox; only stdout enters context.
4. **WEB**: `ctx_fetch_and_index(url, source)` then `ctx_search(queries)`.
5. **INDEX**: `ctx_index(content, source)` — store in FTS5 knowledge base.

### Subagent routing
Spawning subagents (Agent tool) — routing block auto-injected. Bash-type subagents upgraded to general-purpose. No manual instruction needed.

### Output constraints
- Keep responses under 500 words.
- Write artifacts (code, configs, PRDs) to FILES — never inline. Return file path + 1-line description.
- Indexing: use descriptive source labels so others can `ctx_search(source: "label")` later.

### ctx commands
| Command | Action |
|---------|--------|
| `ctx stats` | Call `ctx_stats`, display output verbatim |
| `ctx doctor` | Call `ctx_doctor`, run returned shell command, display as checklist |
| `ctx upgrade` | Call `ctx_upgrade`, run returned shell command, display as checklist |

## Session log archive
Detail in `docs/sessions/`:
- [2026-05-22 patterns (#185–#199)](docs/sessions/2026-05-22-patterns.md) — layered arch / errors / logging / ratchet / test-org.
- [2026-05-23 drain (#235–#251)](docs/sessions/2026-05-23-drain.md) — backend security headers, WebVitals, TOTAL LEADS, Insights, request.state, REVOKE, poller backoff.
- [2026-05-23 Phase 15 audit](docs/sessions/2026-05-23-phase15-audit.md) — pkill LAST-flag, stale-build click, Render no-server lessons.
- [2026-05-23 crossover gaps](docs/sessions/2026-05-23-crossover-gaps.md) — COOP/CORP backport, P0a retraction, docs-PR rebase stack.
- [2026-05-23 branch hygiene](docs/sessions/2026-05-23-branch-hygiene.md) — HEAD-swap between turns; worktree-per-session mitigation.
- [2026-05-23 phase16-t3 data/obs](docs/sessions/2026-05-23-phase16-t3.md) — REVOKE account_deletions, seo_score partial index.
- [2026-05-23 dogfood prep](docs/sessions/2026-05-23-dogfood-prep.md) — demo data + i18n + email plan.
- [2026-05-23 BookBed crossover](docs/sessions/2026-05-23-bookbed-crossover.md) — Phase B Step 2; rate-limit + firestore findings.
- [2026-05-26 Phase 14+15 stack merge](docs/sessions/session_2026-05-26_phase14-15-stack.md) — 21 PRs; chained-base + GH outage admin-merge.
- [2026-05-26 Phase 14+15 sweep + ESLint fix](docs/sessions/session_2026-05-26_phase14-15-sweep.md) — pre-deploy sweep; pytest 1064/0 green; useSyncExternalStore refactor on OfflineBanner cleared eslint=0; ratchet ruff/mypy/pylint deferred; `pre-commit --all-files` splatter recipe.
- [2026-05-26 Phase 14+15 deploy-readiness (parallel)](docs/sessions/session_2026-05-26_phase14-15-readiness.md) — same-day parallel sweep; identical pytest 1064/0; Render blocked at env-var pre-flight (5/7 keys missing in `~/.bookbed-secrets`); ruff F821 `Undefined name 'db'` at backend/main.py:2999/3009 likely PEP-562 false-positive; ruff-format splatters even when scoped.
- [2026-05-27 schema apply + smoke + 2 fixes](docs/sessions/session_2026-05-27_schema-apply-smoke-fixes.md) — Phase 14+15 schema applied via Management API (5→11 tables, live rows untouched); 1158/0 across smoke + Chrome battery; PR #353 ESLint baseline merged + PR #354 proxy `/api/proxy/metrics` 401 fix; `.env` `API_SECRET_KEY=` duplicate-line trap diagnosed + collapsed; schema UNIQUE-constraint `duplicate_table` idempotency deferred per parallel-WIP collision.
- [2026-05-28 issue #363 sweep (13 PRs)](docs/sessions/session_2026-05-28_363_sweep.md) — All 10 buckets of the admin-merge debt cluster closed in one session. PRs #373 (config sweep: baselines + gitleaks-toml + grype-bump + lighthouse-continue-on-error + cov 95→45 + pip-licenses argv), #379 (ssrf_guard py3.14 mypy), #381 (.grype.yaml accepted-risk allowlist), #382 (format-tolerant regex unblocks reformat), #383 (slow_query psycopg `%` + orphans `is_demo=false` + bloat min-rows guard + filters NULL normalize), #384 (ruff-format --all-files 161 files). Plus sibling #378/#309/#306/#250 + 6 dependabot. Main 30900f4 → 49991b0. 154 stale remote branches deleted (165→11). #222 eslint 9→10 deferred — real upstream `eslint-plugin-react` incompat. #332 closed — npm-prod group failed 4 CI checks, asked Dependabot for per-package reissue.

## Operational gotchas (load-bearing)

- **Parallel-session contention**: multiple `claude --dangerously-skip-permissions` against one worktree race on HEAD. Mitigation: dedicated worktree (`git worktree add -b <new> ../<sibling> origin/main`); `git symbolic-ref HEAD` verify after `git checkout -b` AND before EACH write batch; atomic Bash heredoc for stage+commit+push when single-worktree.
- **macOS `pkill -f X -f Y` only honors LAST `-f`**. Use separate calls + verify: `pkill -f X; pkill -f Y; pgrep -f X || echo clean`.
- **chrome-devtools-mcp `.click()` can no-op silently** on stale build with no handler. Add `console.log` at handler entry FIRST + rebuild before concluding handler broken.
- **Render `x-render-routing: no-server` ≠ free-tier sleep** on `plan: starter` (no auto-suspend) → manual pause / deletion / billing / failed deploy. Check dashboard + status.render.com first.
- **Docs-PR stack via sequential rebase** when N>1 docs PRs append to same insertion point. `--force-with-lease=<branch>:<expected-tip>` not bare `--force`. Beyond stack-of-4 → combined PR.
- **`gh run view --log-failed` only surfaces step names** at job-setup-level failures. Open run page in UI when every job fails simultaneously (env-level: expired secret / broken `pip install` / runner config).
- **gitleaks v8.21.2 `--baseline-path` silently misses findings whose `File` contains whitespace** (e.g. `pythins scriptis from gogole collab.md`). Identical-format Fingerprints mismatch only for whitespace paths. Workaround until bump to v8.22+: use `.gitleaks.toml` with `[allowlist] commits=[...]` + `[extend] useDefault = true`. Without `useDefault` the gate becomes a no-op (empty ruleset). See [[gitleaks-v8-baseline-whitespace-bug]] memory.
- **`pre-commit run ruff-format --all-files` splits method-call chains** across lines on any line >88 chars. Tests that grep source text via flat `assertIn('.foo().bar().baz('  on chained calls) break post-format. Use `assertRegex` with `\s*` between method calls + `["\']` char-class for quotes. Pinned by PR #382 after `tests/integration/test_cherry_picks.py:373` surfaced this. See [[ruff-format-brittle-assertion-pattern]].
- **CHECK-constraint dict pairing rule**: adding a CHECK to `supabase_schema.sql` REQUIRES same-PR append to `EXPECTED_CHECK_CONSTRAINTS` in `src/scripts/schema_drift_check.py`. The drift gate compares `pg_constraint` against the Python dict, NOT against parsed SQL. PRs #353/#356/#366 each fell into the trap; PR #377 retroactively appended the 6 missing names. Codified in `docs/db-invariants.md` (PR #380) + `docs/runbooks/apply-phase-14-15-migrations.md` (PR #378). See [[check-constraint-dict-pairing-rule]].
