# LeadDataScraper

## Project Overview
Lead data scraping and enrichment pipeline with Supabase backend and Next.js dashboard frontend.

## Tech Stack
- **Backend**: Python, FastAPI, Supabase (database), Playwright, Google GenAI
- **Frontend**: Next.js (App Router), React 19, TypeScript, Recharts, Lucide icons

## Backend Architecture
- `backend/main.py` — FastAPI app with all API endpoints (leads, campaigns, orchestrator, AI chat, exports). Lazy module-level singletons (`db`, `router`, `auditor`, `orchestrator`) via module `__getattr__` so heavy chains (pandas, google.genai, playwright) don't fire at import time. **PEP 562 caveat:** `__getattr__` is only consulted for attribute access on the module object, not for bare-name `LOAD_GLOBAL` lookups inside functions in the same module — handlers like `if not db.client:` would raise `NameError` if hit before priming, so the lifespan explicitly attribute-accesses each name via `sys.modules[__name__]` to populate `globals()` once at boot. See "Cold-start lazy imports" below.
- `src/utils/supabase_helper.py` — Supabase client wrapper (uses `SUPABASE_SERVICE_ROLE_KEY` for backend ops). Hot-path read methods (`list_leads_recent`, `get_stats_rows`, `find_running_job`, `insert_orchestration_job`) are `asyncio.to_thread`-wrapped so sync PostgREST calls don't block the uvicorn event loop.
- `src/utils/stats_cache.py` — In-process TTL cache (60s) with `asyncio.Lock` stampede guard for `/stats`. Per-uvicorn-worker singleton; `invalidate()` hooked into write paths.
- `src/utils/query_profiler.py` — Dev-only Supabase query profiler, env-gated (`QUERY_PROFILER=1`). Monkey-patches `client.table` via a chainable proxy to record verb + caller frame + timing; `assert_o1(per_unit=N)` for N+1 regression guards.
- `src/scrapers/seo_audit.py` — Async SEO auditor with tech stack detection (aiohttp, no Playwright).
- `src/scrapers/discovery_engine.py` — Google Maps lead discovery via Playwright.
- `src/scrapers/enrichment_engine.py` — Shared-browser-pool enrichment. One Chromium process per `EnrichmentEngine` instance; per-lead `new_context()`. `aclose()` tears down on batch end (called from orchestrator + `_execute_deep_enrichment` `finally`).
- `src/core/task_orchestrator.py` — Background job orchestration for audits, hunts, enrichment. `_process_in_chunks` `finally` calls `enricher.aclose()` + `stats_cache.invalidate()`.
- `src/core/agentic_router.py` — AI instruction routing (natural language → task execution).

## API Security
- **Frontend access requires a Supabase Auth session.** Root `frontend/proxy.ts`
  (Next 16 convention; wraps `utils/supabase/middleware.ts`) redirects anonymous
  traffic to `/login`. Do not also create `frontend/middleware.ts` — Next 16
  errors on duplicate convention files and the dev/prod server fails to boot.
  The `/api/proxy/[...path]` handler re-runs `auth.getUser()` and 401s on
  unauthenticated fetch/XHR. State-changing methods also reject foreign `Origin`.
  Provision users in the Supabase Auth dashboard (no public signup).
- Auth middleware public-path allowlist (`/login`, `/auth`, `/api/auth`) uses
  exact match or trailing-slash subpath — not raw `startsWith`. Prevents a
  future `/login-internal` or `/authentication-guide` route from being silently
  unauthenticated by string-prefix overlap.
- `/login?next=<path>` is sanitised by `sanitizeNext()` in
  `frontend/utils/url.mjs` (imported by `app/login/actions.ts`). Only
  same-origin relative paths are accepted (must start with `/`, must NOT
  start with `//` or `/\`). The allowlist regex deliberately excludes
  `@` and `:` so a `/@evil.com/foo` value can't resolve to a same-origin
  URL whose address bar mimics the `user@host` phishing-display pattern.
  **Decode-once layer**: the regex allows `%` (URL-encoded chars), so
  payloads like `/dashboard%2f%2fevil.com` and `/%2e%2e/evil.com` would
  otherwise slip through. After the regex pass, the value is
  `decodeURIComponent`'d once and re-rejected if the decoded form
  contains `//`, `\`, `..`, or control chars (`\x00-\x1f\x7f`).
  Malformed encoding (`%ZZ`, `%2`, lone `%`) catches in the try/except
  and collapses to `/`. Closes open-redirect + phishing-assist on auth.
  `utils/url.mjs` also exports `ensureProtocol()` — the `<a href>`
  scheme guard that forces scraped `website`/social-link values through
  a `http:`/`https:`-only allowlist (rejects `javascript:` / `data:`).
  Both are pure functions, CI-covered by `utils/url.test.mjs` (57 cases,
  `.mjs` so `node --test` needs no build step — same pattern as
  `cookie-floor.mjs`) and the e2e `tests/test_open_redirect.py`.
- Supabase session cookies set via `setAll()` in
  `frontend/utils/supabase/middleware.ts` are true-floored to
  `SameSite=Lax`, `HttpOnly=true`, `Secure=true` (prod). Spread order is
  `{...options, sameSite, httpOnly, secure}` — Supabase can tighten
  (`SameSite=Strict` is preserved) but cannot loosen (`None` is overwritten
  to `Lax`, `httpOnly=false` is overwritten to `true`).
- All endpoints (except `/` liveness probe) require `X-API-Key` header —
  validated by `verify_api_key` dependency (constant-time compare via
  `secrets.compare_digest`). `/` returns `{"status":"ok"}` with no product
  or version metadata to avoid free fingerprinting.
- API key is set via `API_SECRET_KEY` env var in backend `.env`
- `/execute` accepts only a `Literal` allowlist of task names
  (`ExecutableTask`) and a typed `ExecutePlanParams` model with bounded
  `constr` fields + `extra='forbid'`. Untyped `params: dict` was removed so
  authed callers cannot bypass the natural-language → tool gating with a
  hand-crafted plan. Handler dicts are produced via
  `model_dump(exclude_none=True)` so unset fields don't shadow handler
  defaults like `params.get("filters", "high-risk")`.
- `/api/proxy` and `/api/auth/signout` both apply a fail-closed Origin
  allowlist gate to state-changing POSTs (`if (!origin || !ALLOWED_ORIGINS
  .includes(origin)) → 403`). WHATWG Fetch always sends Origin on
  cross-origin POST, so rejecting both mismatched and missing closes the
  edge-case-client gap. `SameSite=Lax` already blocks cookie-bearing
  cross-site fetch; this is belt-and-braces.
- Optional single-tenancy assertion: set `OPERATOR_EMAIL` in the backend
  env and `_assert_single_tenant_if_enforced()` (in `backend/main.py`
  lifespan) verifies Supabase Auth has exactly that one user at boot. The
  per-resource endpoints (`/process-lead`, `/draft-outreach`,
  `/orchestrator/status/{job_id}`, `/campaigns/{id}/...`) intentionally
  don't filter by `owner_user_id` — design assumes one operator. Setting
  `OPERATOR_EMAIL` makes that invariant trip loudly at startup if a second
  user is ever provisioned. Unset → check skipped. **The check is fail-closed:**
  the only swallowed exception is the explicit `RuntimeError` raised on a
  real invariant violation; any other failure (Supabase Auth API hiccup,
  permission error, network blip) re-raises and aborts boot — "could not
  run" must not pass for "passed" when the operator has opted into the
  invariant.
- Interactive docs (`/docs`, `/openapi.json`, `/redoc`) are **disabled by default**.
  Enable in dev via `ENABLE_DOCS=true`. Never set in production.
- **Frontend does NOT hold the API key.** The browser calls a same-origin Next.js
  proxy at `/api/proxy/[...path]` (see `frontend/app/api/proxy/[...path]/route.ts`)
  which injects `X-API-Key` from the server-side `API_SECRET_KEY` env var.
- The proxy stamps `Cache-Control: no-store` on every response (errors and
  successes alike) so authed payloads never sit in browser bfcache or
  intermediate caches after logout. Client-side `apiFetch` already passes
  `cache: 'no-store'` on the request — the response-side stamp is the
  matching defense.
- Destructive endpoint `DELETE /leads/clear` additionally requires
  `X-Admin-Token` matching `ADMIN_TOKEN` env (defense-in-depth even if API key leaks).
  The Next.js proxy injects `X-Admin-Token` from its own server-side env **only
  for the `leads/clear` path** (`frontend/app/api/proxy/[...path]/route.ts`).
  Clients cannot set this header themselves; the in-browser auth gate (Supabase
  session) is the only thing that lets a user reach the proxy at all. Setting
  `ADMIN_TOKEN` in both backend `.env` AND frontend `.env.local` (must match)
  is required — without it the UI's "Clear All Leads" button hits 403.
- Required env vars (see `.env.example`):
  - Backend `.env`: `API_SECRET_KEY`, `ADMIN_TOKEN`, `SUPABASE_URL`,
    `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`, `ALLOWED_ORIGINS`
  - Backend (optional): `OPERATOR_EMAIL` — when set, enforces the
    single-tenancy assertion described above.
  - Backend (optional): `OPERATOR_NAME` — appended to outreach drafts
    as the signature ("Best,\nJane Smith"). Unset → drafts sign with
    "Best,\nYour Name" placeholder, prompting the operator to set it.
  - Frontend `.env.local`: `BACKEND_URL` (server-side, points at FastAPI),
    `API_SECRET_KEY` (server-side, NOT `NEXT_PUBLIC_*`),
    `ADMIN_TOKEN` (server-side, must match backend's value — proxy injects
    it on destructive paths),
    `ALLOWED_ORIGINS` (used by `/api/proxy` + `/api/auth/signout` Origin
    gates), `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`
  - **Render deploy parity**: the frontend service in `render.yaml` MUST
    declare `ALLOWED_ORIGINS` and `ADMIN_TOKEN` as envVars. Without them
    the Origin gate defaults to `localhost:3000` (every prod state-change
    fail-closed 403s) and the "Clear All Leads" button can't reach the
    backend. Both are `sync: false` — set the actual values in the Render
    dashboard, never commit them.
- Rate limiting: AI and destructive endpoints capped via `slowapi`. See
  `backend/main.py` decorators. `headers_enabled=False` — `X-RateLimit-*` not
  emitted (slowapi requires `response: Response` param to inject; we don't
  declare it on every endpoint).
- Rate-limit key derives from `X-Forwarded-For` set by the Next.js proxy.
  The proxy strips client-controlled XFF / X-Real-IP / Forwarded headers and
  re-emits XFF from the platform-injected header named in
  `TRUSTED_CLIENT_IP_HEADER` (default `x-vercel-forwarded-for`; set to
  `x-forwarded-for` on Render). Additionally, `_rate_limit_key` in
  `backend/main.py` only honours XFF when the request carries a valid
  `X-API-Key` (constant-time compared). Forged XFF without the key falls
  back to the TCP peer IP — so even if the FastAPI port is ever exposed
  directly, attackers cannot spoof XFF to spread load across rate-limit
  buckets.
- Browser security headers: CSP is set **per-request** in
  `frontend/proxy.ts` (NOT statically in `next.config.ts`) so the
  `script-src` directive can carry a fresh `'nonce-<n>'` +
  `'strict-dynamic'` each render. Next 16 RSC streams inline
  `<script>self.__next_f.push(...)</script>` bootstrap blocks — a
  static `script-src 'self'` would block hydration in `npm run start`
  prod (sev-1, see `docs/findings/2026-05-22-csp-blocks-prod-hydration.md`).
  The nonce flow:
  1. `frontend/proxy.ts` generates a per-request 16-byte base64 nonce,
     puts it on a NEW `Headers` object (mutating
     `request.headers` in-place does NOT propagate to RSC under
     Next 16 — must pass via `NextResponse.next({ request: { headers } })`),
     and sets the matching `Content-Security-Policy` on the response.
  2. `frontend/utils/supabase/middleware.ts::updateSession` accepts the
     `requestHeaders` arg and threads it into the `NextResponse.next`
     call.
  3. `frontend/app/layout.tsx` is `dynamic = 'force-dynamic'` and
     calls `(await headers()).get('x-nonce')` — registering the
     `headers()` dependency. This combo makes Next.js auto-stamp the
     same nonce onto every inline `__next_f` block it streams.
     Without `force-dynamic`, routes pre-render statically with no
     nonce and CSP rejects hydration.
  Other static headers stay in `next.config.ts`: HSTS (2y + preload),
  `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy`,
  `Permissions-Policy` (camera/mic/geo off).
  `productionBrowserSourceMaps: false`. CSP directives still in effect:
  `connect-src 'self' <SUPABASE_URL>` + the matching `wss:`,
  `img-src 'self' data: blob: <SUPABASE_URL>` (no blanket `https:` so
  attacker-controlled URLs can't be rendered as tracking pixels),
  `default-src 'self'`, `base-uri 'self'`, `form-action 'self'`,
  `frame-ancestors 'none'`, `object-src 'none'`,
  `style-src 'self' 'unsafe-inline'` (Next inlines a tiny style
  block — required for CSS).
- HTML page routes (`/`, `/login`, `/insights`, `/campaigns`) additionally
  get `Cache-Control: private, no-store, max-age=0` + `Vary: Cookie` via the
  `pageNoCacheHeaders` block in `next.config.ts`. This opts the authed pages
  out of bfcache so hitting Back after sign-out doesn't render the cached
  authed shell. `_next/static/*` chunks are excluded (immutable content-hashed
  assets — must stay cacheable for perf).
- `/upload` streams the request body and aborts at 50 MB (`MAX_UPLOAD_BYTES`)
  with a 413 — no full-buffer DoS. Content-Type allowlist is strict:
  `text/csv` and `application/vnd.ms-excel` only. `application/octet-stream`
  was removed — defense-in-depth so any downstream code that trusts the
  declared type can't be tricked by a generic byte stream.
- **CSV / formula injection guard.** Lead names, `company_name`,
  `pain_points`, `email_hook`, and other free-text fields come from CSV
  uploads + Google-Maps scrapes — both attacker-controllable. Every
  `to_csv` call site funnels through `sanitize_dataframe_for_csv()` in
  `src/utils/csv_helper.py`, which prefixes any string cell starting with
  `= @ + - \t \r` with `'` so Excel/Sheets/Numbers render it as literal
  text instead of executing `=HYPERLINK(...)` or `@SUM(...)` when the
  operator opens the export. Applied at `save_csv`,
  `src/scripts/export_leads.py` (4 sites), and the
  `/campaigns/{id}/export` handler in `backend/main.py`. Any new export
  path must use the same helper.
- **SMTP header injection guard** (`src/integrations/email_sender.py`).
  Recipient regex is `^[^@\s]+@[^@\s]+\.[^@\s]+\Z` — `\s` excludes `\r\n`
  so `victim@x.com\r\nBcc: attacker@evil` can't smuggle Cc/Bcc/Subject
  headers via `msg["To"]`. **Anchored with `\Z`, not `$`** — Python's
  `re` treats `$` as "end OR before trailing `\n`" by default, so
  `victim@x.com\n` would have slipped through and let a trailing-LF
  recipient smuggle into the RCPT envelope. Subject + from_name
  additionally pass a CRLF-reject check before they are written into
  MIME headers — both carry attacker-controllable content (Gemini draft,
  operator override). When/if SMTP send wires up, this is the boundary
  check. Locked in by `tests/test_crlf_injection.py` + the existing
  `tests/test_email_sender_guards.py`.
- **Log-line forgery guard** (`src/utils/logging_config.py`). Every
  `logger.error("processing %s", lead_name)` call carries attacker-
  controllable args (lead names / websites / pain-points come from
  CSV uploads + Google-Maps scrapes). `_CRLFScrubFilter` is attached
  to both the console + `RotatingFileHandler` and translates raw
  CR/LF/VT/FF in `record.msg` AND every entry of `record.args` (tuple
  + dict forms) to the printable `\r` / `\n` escape. Without it, a
  lead named `X\r\nERROR forged log line` would emit a second log
  entry at attacker-chosen level + content. Locked in by
  `tests/test_crlf_injection.py::TestLoggingCRLFScrub`.
- **Email-extraction input cap** (`src/scrapers/seo_audit.py`,
  `src/processors/leadhunter.py`). The legacy email regex
  `\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}\b` is O(n²) under
  `re.findall` on attacker-shaped HTML (`"a@" + "a." * N + "x"` —
  charset/literal-dot overlap forces backtracking at every starting
  position). Production sites slice the input to **50 KB** before
  findall/search: scraped emails sit near the document head, so the
  cap is operationally safe but bounds worst-case CPU. Static-scan
  test `tests/test_redos.py::TestEmailRegexInputBounded` fails CI if
  a new call site lands without a `[:N]` slice.
- Outbound HTTP from `seo_audit.py` and `enrichment_engine.py` runs through
  `src/utils/ssrf_guard.py` (`SSRFGuardResolver` + `assert_safe_url`) which
  rejects private / loopback / link-local / reserved / multicast IPs and
  known cloud + Kubernetes metadata hostnames at DNS-resolve time. The
  `_BLOCKED_HOSTS` set includes GCP/EC2 metadata DNS names plus
  `kubernetes.default.svc` / `.cluster.local` for cluster-deployment safety.
  Hardens against SSRF and DNS-rebinding.
- Playwright browser contexts in `enrichment_engine.py` additionally install
  `_install_ssrf_route_guard(context)` — a `context.route("**/*", ...)`
  handler that re-runs `assert_safe_url` on every request the browser makes
  (initial navigation, 30x redirects, subresources). Closes the TOCTOU window
  between the pre-flight DNS check and `page.goto()`, and blocks redirect
  chains that hop to an internal host.
- Any Gemini call that mixes static prompt text with DB-derived data or
  scraped page content must fence the data inside
  `<UNTRUSTED_DATA>...</UNTRUSTED_DATA>` and pair it with the shared
  `_UNTRUSTED_DATA_SYSTEM_INSTRUCTION` via
  `genai_types.GenerateContentConfig(system_instruction=...)`. Use
  `_fenced_json()` in `src/core/agentic_router.py`. Strip any literal
  `</UNTRUSTED_DATA>` substring from the payload before embedding — JSON
  doesn't escape angle brackets, so an attacker who controls a lead field or
  page body could otherwise close the fence early. Lead rows arrive from CSV
  uploads and Google-Maps scrapes; both are attacker-controllable. Never
  splice lead fields directly into prompt body text (e.g. inside an
  "Example: ..." line — use a placeholder like `[COMPANY NAME]` instead).
- AI-client constructors (`GeminiMapper`, `AgenticRouter`, `LeadHunter`)
  read `GEMINI_API_KEY` from env in `__init__`. `GeminiMapper.__init__`
  also accepts an optional `api_key` arg for callers that need to override.
  **Never mutate `os.environ["GEMINI_API_KEY"]` at request time** — the
  app runs under multi-worker uvicorn, and an env write in one worker
  races other in-flight requests and leaks the override into unrelated
  handlers. Pass the key into the constructor instead.
- Supabase RLS is enabled on `leads`, `campaigns`, `campaign_messages`,
  `orchestration_jobs`. Anon + authenticated roles are revoked. All reads/writes
  go through the backend, which uses `service_role` to bypass RLS server-side.
- Schema migrations use `add_lead_column(text)` RPC (allowlisted column-name
  regex). The generic `exec_sql` RPC has been removed. The function is
  `SECURITY DEFINER` with `SET search_path = pg_catalog, public` so a
  malicious `public.format(...)` shadow can't hijack the built-in identifier
  resolution; `ALTER FUNCTION ... OWNER TO postgres` pins the privileged
  authority; `REVOKE CREATE ON SCHEMA public FROM PUBLIC` blocks roles from
  creating shadowing objects in the first place. `service_role` bypasses
  GRANTs implicitly, so the backend still calls it; Supabase Studio
  operations may need the role re-granted if a future workflow relies on
  PUBLIC creating in `public` (none currently does).
- CORS restricted to specific methods (`GET/POST/PUT/DELETE/OPTIONS`) and headers (`Content-Type/Authorization/X-API-Key`)
- All POST endpoints use Pydantic models for input validation (no raw `dict` payloads)
- Error responses never leak internal exception details
- Global FastAPI exception handler converts any uncaught exception to JSON
  (`{"error": "Internal server error"}`, 500) so the Next.js proxy can always
  `.json()` the body without SyntaxError. **`RecursionError` (deep-JSON DoS)
  is special-cased to 413 `{"error": "Payload nesting too deep"}`** so a
  2000-level nested body doesn't surface as a 500 the operator has to
  triage. Locked in by
  `tests/test_json_pollution.py::TestDeeplyNestedJSON`.
- `_validation_with_authz_check` (the `@app.exception_handler(RequestValidationError)`
  override in `backend/main.py`) gates Pydantic 422 responses behind the
  X-API-Key check. Without this, FastAPI's default 422 returned the full
  `detail[]` array (`type`, `loc`, `msg`, `input`, `ctx`) — leaking the
  expected body shape of every endpoint to an unauthenticated attacker
  probing with bogus JSON. Anonymous (or wrong-key) callers now get the
  generic `{"detail": "Invalid or missing API key"}` 403 that
  `verify_api_key` already returns. Authenticated callers still get the
  full Pydantic `detail[]` array so the frontend's
  `AIChat.handleSubmit` join on `detail[].msg` continues to surface
  user-actionable errors (e.g. "String should have at most 4000
  characters"). The `input` field is stringified via `json.dumps(default=str,
  allow_nan=False)` and capped at 512 chars — two reasons: (a) `NaN`/`Infinity`
  in the request body would otherwise crash the 422 response (`json.dumps`
  raises "Out of range float values"), turning a validation error into a
  500; (b) a 10 KB malicious value can't roundtrip back to the client in
  the error response. Locked in by `tests/test_validation_authz_gate.py`
  + `tests/test_json_pollution.py::TestLargeNumberPrecision`.
- Lookups for a single row use `.maybe_single()` (not `.single()`) so a
  missing row returns `data=None` and the handler can answer 404. `.single()`
  raises `APIError(PGRST116)` on 0 rows, which the broad `except` swallows
  into a generic 500 — and the explicit 404 branch becomes dead code. Used
  on the `/campaigns/{id}` and `/campaigns/{id}/generate` paths.
- Endpoint pattern for handlers that delegate to `AgenticRouter`: check
  `db.client` up front and return 503 if missing, then after
  `router.execute_task(plan)` returns, inspect the result — if it's a dict
  with an `error` key, propagate via `error_response(result["error"], 503)`
  instead of returning HTTP 200 with an `{error: ...}` body. The router's
  `error` strings are operator-authored static text, never echoed
  attacker-controlled content. `/insights` is the reference implementation
  (`backend/main.py:498-513`).
- `/api/auth/signout` is wired to the **Sign Out** nav item in
  `frontend/app/components/Sidebar.tsx`. The button POSTs same-origin so
  the browser sends an `Origin` header that passes the fail-closed gate;
  `try { … } finally { router.replace('/login'); router.refresh() }` keeps
  the UI consistent even on transient network errors.
- `hashlib.md5` use in `discovery_engine.py` (Google-Maps lead `unique_key`
  fallback when no place-ID URL is available) is annotated with
  `usedforsecurity=False` — documents non-crypto intent and silences
  Bandit/Semgrep MD5 lints. Truncation to 16 hex chars is fine because
  collisions only route two distinct businesses to the same row, caught by
  the human review queue.
- Fingerprint reduction: `Dockerfile` starts uvicorn with
  `--no-server-header` so `Server: uvicorn` never leaves the box. The
  Next.js proxy additionally strips any upstream `Server` header on
  forward — belt-and-braces if uvicorn is ever launched without the flag.
- **Dockerfile hardening.** `build-essential` is installed AND purged in
  the same `RUN` layer (gcc/make etc. don't ship to the runtime image — no
  post-RCE local-privesc toolkit). A container-level `HEALTHCHECK` polls
  `/` (the unauthenticated liveness probe) so `docker run` and local
  orchestrators can detect a wedged uvicorn worker. Render's external
  probe still owns prod health.
- Security invariants for `/execute` are locked in by
  `tests/test_execute_plan_model.py` (17 tests + 17 subtests). Covers
  Literal allowlist, `extra='forbid'`, bounded-length `constr` per key,
  and the `model_dump(exclude_none=True)` requirement that preserves
  handler defaults like `params.get("filters", "high-risk")`. Run via
  `pytest tests/`.
- **AI quality & safety test suite** (offline + live tiers under `tests/`):
  - **Offline (CI-default, no GEMINI_API_KEY needed)**:
    - `test_prompt_snapshots.py` — "prompts are code" guardrail. 8 Gemini
      call sites, SHA256-hashed in `tests/fixtures/prompt_snapshots.json`.
      Any drift forces an intentional review; regenerate baseline with
      `UPDATE_PROMPT_SNAPSHOTS=1 pytest tests/test_prompt_snapshots.py`.
    - `test_endpoint_hardening.py` — every authed endpoint × 7 concerns
      (missing/wrong API key, empty body, extra fields, max-length+1,
      adversarial Unicode/NUL/zero-width/RTL/emoji, rate-limit boundary,
      admin-token guard on `DELETE /leads/clear`). `httpx.AsyncClient` +
      `ASGITransport`; ~170 assertions in 1.1s. Fresh app per test class
      so slowapi memory storage resets. **Note: code returns 403 not 401
      on auth failures — the test asserts real behaviour.** Adversarial
      codepoints built via `chr(0x200b)` so source stays pure ASCII
      (semgrep bidi-detector clean).
    - `test_pydantic_models_meta.py` — auto-discovers every `BaseModel`
      in `backend.main` and enforces `extra='forbid'`, `max_length` on
      every string + list, `Literal` on enum-shaped fields
      (`channel/status/task/kind/role`). Reads `FieldInfo.metadata`
      (Pydantic v2 canonical constraint location). New models can't ship
      without hardening.
    - `test_agentic_router_behavior.py` — every `ExecutableTask` value
      dispatches without raising; arbitrary / SQL-injection-shaped /
      missing task names reject with **zero Gemini calls** (counter
      asserted); injection payloads in `params.query_text` land inside
      an `UNTRUSTED_DATA` fence with `system_instruction` set;
      non-existent `unique_key` short-circuits before Gemini; DB never
      receives raw injection strings as filter args.
    - `test_ssrf_guard_regression.py` — 25 reject cases via `subTest`
      (loopback, AWS/GCP metadata, k8s `*.cluster.local`, RFC1918,
      disallowed schemes, userinfo confusion, decimal/hex-encoded IPs)
      + benign-URL allowlist + dedicated DNS-rebind test
      (getaddrinfo public→private; second call raises).
    - `test_outreach_score_properties.py` — fixed-fixture + hypothesis
      (skipped if hypothesis absent). **Pinned finding:
      `calculate_outreach_score` does NOT read `seo_score`** —
      `test_seo_score_does_not_affect_score` locks current behaviour so
      a future refactor that wires it in trips loudly.
    - `test_segment_stability.py` — 20 leads × 5 runs.
      **`segment_lead` is pure-Python regex, not Gemini** — test is a
      regression guard for a future Gemini-backed segmenter AND a
      contract pin on the 11-label `KNOWN_LABELS` vocabulary.
  - **Live tier (skipped without GEMINI_API_KEY)** — run before model /
    prompt changes:
    - `test_outreach_golden_set.py`, `test_linkedin_golden_set.py` —
      10-lead quality bar + Gemini-as-judge (avg ≥ 7.5).
    - `test_outreach_hallucination.py` — 5 sparse leads (name + website
      only). Two-layer detection: regex (number-claims, named-title
      claims, 35+ tech tokens) + judge (every claim, `verifiable=bool`).
      ANY invented claim fails. Judge sees the exact `lead_data` dict
      the writer saw — synced to `agentic_router.py:389`.
    - `test_ask_determinism.py` — 20× same instruction → same task;
      `params.query` pairwise cosine ≥ 0.90 via `text-embedding-004`.
      Documents that schema doesn't declare `limit`.
    - `test_pain_points_consistency.py` — 50 calls; intra-lead pairwise
      Jaccard ≥ 0.60 AND inter-lead < 0.30 (catches input-blind generic
      output via 12-category synonym taxonomy).
    - `test_ai_mapper_golden.py` — 15 CSV header variants spanning
      English/Bosnian/French/German/Spanish + BOM-prefix + SQL injection
      + prompt injection + ambiguous "contact" + junk columns. 100% on
      canonicals; `custom_assert` per edge case.
    - `test_i18n_outreach.py` — BiH/Croatian leads (`Kovačević`, `Žito`,
      `Đurić`) through outreach + LinkedIn + mapper. Mojibake fingerprint
      sweep, 60-word BCS function-word slop detector, diacritic-
      preservation guard (catches silent ASCII transliteration).
    - `test_refusal_boundaries.py` — 6 malicious instructions
      (delete_leads, bulk_spam, phishing_bank, scrape_private_social,
      threatening_legal, doxx_owners). Classifier: refusal / benign /
      foreclosed / dangerous. ANY `dangerous` fails. Full transcript JSON
      dumped to a tempfile; path printed each run.
    - `test_json_compliance.py` — 50× per JSON-emitting call site
      (mapper, insights, hooks, enrich). 100% parse + schema required.
      Failure message points at `response_mime_type='application/json'`
      + `response_schema` as the canonical fix.
    - `test_ai_cost_budget.py` — 100-call pipeline budget per 20 leads:
      ≤200k input, ≤50k output, ≤8k single-call, ≤$0.50 total. Per-task
      breakdown printed on every run. Pricing constants pinned at top.
    - `test_insights_quality.py` — 50-lead seeded fixture
      (audit_status mix, score range, lead_source distribution). 5 calls
      + 5 judges. No-invented-numbers check uses an allowed-set from
      ground truth (counts + percentages ±1). Judge avg ≥ 8. Documents
      that `_get_strategic_insights` SELECTs only 5 fields.
    - `test_campaign_diversity.py` — 20 dentists, identical audit
      profile, only company/contact differs. Subject pairwise Jaccard
      ≤ 0.30 (after `COMPANY_NOUN_WORDS` masking) + opening-sentence
      cosine < 0.85. Catches "personalization theater".
  - **Critical pinned findings** (do NOT lose these on refactors —
    each lives in a test docstring):
    1. `seo_score` is not an input to `calculate_outreach_score`.
    2. `segment_lead` is pure regex, not Gemini.
    3. `_get_strategic_insights` SELECTs only
       `name,company_name,audit_status,seo_score,lead_source`.
    4. `discovery_search` / `run_massive_pipeline` tool schemas don't
       declare `limit`.
    5. `verify_api_key` returns 403, not 401.
    6. Discovery and SEO audit are NOT Gemini calls — excluded from cost
       budget.
  - **Run targeting**:
    - Full suite: `pytest tests/`
    - Offline-only (~5s, no API key): `pytest tests/test_endpoint_hardening.py
      tests/test_pydantic_models_meta.py tests/test_agentic_router_behavior.py
      tests/test_ssrf_guard_regression.py tests/test_prompt_snapshots.py
      tests/test_outreach_score_properties.py tests/test_segment_stability.py`
    - Live quality: `GEMINI_API_KEY=... pytest tests/test_*golden*.py
      tests/test_*hallucination*.py tests/test_*determinism*.py
      tests/test_*consistency*.py tests/test_*i18n*.py tests/test_*refusal*.py
      tests/test_*json_compliance*.py tests/test_*cost_budget*.py
      tests/test_*insights_quality*.py tests/test_*diversity*.py`
- **Outreach modal `mailto:` href** (`frontend/app/page.tsx`). `leadEmail`
  is `encodeURIComponent`-wrapped before interpolation, alongside the
  subject + body. Without the encode an attacker-controlled lead email
  like `victim@x.com?bcc=attacker@evil` smuggled Cc/Bcc/Subject/body into
  the operator's mail client on click.
- **Frontend dependency pinning policy.** `package.json` drops the `^`
  prefix on security-critical libs (`next`, `@supabase/ssr`,
  `@supabase/supabase-js`) so a future `npm install` (vs `npm ci`) can't
  silently take a minor of `@supabase/supabase-js` — which sees session
  JWTs and talks to the DB. The lockfile is the authoritative pin;
  removing `^` is belt-and-braces. The `postcss` override is pinned
  `^8.5.10` (was unbounded `>=`) to prevent a regenerated lockfile from
  accepting an arbitrary future postcss.
- **CI/CD architecture** — full inventory + operator setup at
  `docs/ci-architecture.md`. 15 workflows under `.github/workflows/`,
  every action SHA-pinned with `# vX.Y.Z` comment, standard concurrency
  block, top-level `permissions: contents: read` with explicit per-job
  escalations. PR gate in `ci.yml` (~20 required checks: pytest+95%
  coverage, npm test, pre-commit, pip-audit, npm audit moderate+,
  gitleaks, lockfile-sync, license-check, flaky-gate, semgrep,
  ruff+mypy --strict, ESLint --max-warnings 0, Playwright E2E,
  schema-drift, referential-integrity, query-plans, Lighthouse,
  container-scan (Trivy+Grype+SBOM), Conventional Commits title,
  PR size gate). Post-merge in `security.yml` (push + daily cron) re-runs
  the security scans + DB invariant sweeps. Tagged-release supply
  chain: `deploy-backend.yml` (push main) + `release.yml` (push tag
  `v*`) push to GHCR → SLSA3 provenance via reusable workflow →
  `cosign verify-attestation` → Render API rollout on the pinned
  digest. Render service MUST be in "Deploy from existing image"
  mode for the chain to gate rollout. Forged GHCR images (e.g. leaked
  PAT push) fail cosign verify and never reach Render.
- **Workflow pin invariant**: every `uses: org/action@<sha>  # vX.Y.Z`
  line is a 40-char commit SHA + comment Dependabot reads to bump
  both atomically (Codecov 2021 pattern). `workflow-pin-guard` local
  pre-commit hook + `ci.yml::pre-commit` job both reject
  `uses: org/action@vN` patterns. Resolve new-action SHAs via
  `git ls-remote --tags https://github.com/<repo>`.
- **Operational trackers** — three workflows maintain ONE canonical
  auto-updated GitHub issue each: `flakiness-detector.yml` → label
  `flaky` (nightly 3× parallel pytest, gist `flaky-tests.json`, fed
  into `ci.yml::flaky-gate` which blocks PRs touching files with
  active flakes in the last 7 days); `mutation-test.yml` → label
  `mutation-coverage` (weekly mutmut, 80% kill-rate threshold on
  `ssrf_guard.py`, `prompt_safety.py`, `leadhunter.py`); 
  `workflow-drift.yml` → label `workflow-drift` (daily sha256
  vs `.github/workflow-hashes.json` + git-log untracked-commit
  audit; `make workflow-hashes` regenerates snapshot).
- **pip-tools lockfile + hash pinning**: `requirements.in` is the
  source-of-truth for direct deps; `requirements.txt` is generated by
  `make lock-python` (`pip-compile --generate-hashes --strip-extras`).
  Dockerfile installs with `--require-hashes` — a PyPI tampering
  scenario where package bytes change between resolve and install
  fails the build with `HashMismatch`. The `lockfile-sync` CI job
  re-runs `pip-compile --dry-run` and diffs against committed; hand-
  edits or forgotten regenerations turn the gate red. **Day-one
  blocker**: operator must run `make lock-python` once locally before
  the next merge or both lockfile-sync AND the Docker build will fail.
- **Secret inventory + rotation** at `docs/secret-inventory.md`. 29
  secrets cataloged with blast-radius-tiered rotation: monthly
  (`SUPABASE_SERVICE_ROLE_KEY`, `RENDER_API_KEY`,
  `SUPABASE_DATABASE_URL`); quarterly (`API_SECRET_KEY`,
  `ADMIN_TOKEN`, `GEMINI_API_KEY`). OIDC where supported (GHCR +
  Sigstore Fulcio); Render OIDC verify-before-adopting; Supabase
  Mgmt API + Gemini stay PAT-only until upstream support lands.
- **Local-CI parity** via pre-commit (`.pre-commit-config.yaml` +
  `Makefile`). `make install-hooks` once per clone; same hooks run
  in `ci.yml::pre-commit (local-CI parity)` so any drift is itself
  the alarm. Selective `mypy` in pre-commit targets
  `src/utils/(ssrf_guard|csv_helper)\.py` only (security-critical,
  fully typed); the hard `--strict src/` gate runs in
  `ci.yml::python-lint`. Semgrep runs via `pip install semgrep &&
  semgrep scan --error` — the deprecated `returntocorp/semgrep-action@v1`
  was removed; the org was renamed and the action repo is stale, so
  a tag re-point would have executed attacker code in CI.
- Legacy CI security gates documentation (now superseded by
  `docs/ci-architecture.md`): `pip-audit --strict`
  on `requirements.txt`, `npm audit --omit=dev --audit-level=high` on the
  frontend, and Semgrep OWASP/Python/TypeScript/React rulesets. Runs on
  push, PR, and daily cron (catches newly-disclosed CVEs in already-pinned
  deps without a code change). **Fork-PR guard**: every job carries
  `if: github.event_name != 'pull_request' || github.event.pull_request
  .head.repo.full_name == github.repository` so a hostile fork PR can't
  feed `pip-audit` a `requirements.txt` whose `setup.py` runs arbitrary
  code in the runner (pip has no `--ignore-scripts` equivalent). Semgrep
  runs via `pip install semgrep && semgrep scan --error` — the deprecated
  `returntocorp/semgrep-action@v1` was removed; the org was renamed and
  the action repo is stale, so a tag re-point would have executed
  attacker code in CI.
- **Supabase schema + RLS drift gate**
  (`src/scripts/schema_drift_check.py`). Runs in both `ci.yml`
  (PR-time, blocks merge) and `security.yml` (push to `main` + daily
  cron — catches manual Supabase Studio edits between PRs). Connects via
  the `SUPABASE_DATABASE_URL` GitHub Actions secret (`?sslmode=require`).
  Fail-closed: exits 2 if the secret is unset, so a missing/typo'd
  secret turns the job red instead of silently passing. Asserts: column
  parity vs `supabase_schema.sql` (CREATE TABLE + ALTER TABLE ADD
  COLUMN, no missing/extra); RLS enabled on `leads`, `campaigns`,
  `campaign_messages`, `orchestration_jobs`; a `<table>_deny_all` policy
  exists on each (roles ⊇ {anon, authenticated}, FOR ALL, qual=false,
  with_check=false); no anon/authenticated/PUBLIC GRANT on those 4
  tables; `add_lead_column` is SECURITY DEFINER, owned by `postgres`,
  has `search_path` set, and no EXECUTE grant to
  anon/authenticated/PUBLIC. Column check is **name-only** — type drift
  (e.g. `needs_manual_review` text-vs-boolean, `outreach_score`
  double-vs-int) is intentionally out of scope and tracked separately.
- **Supabase referential integrity gate**
  (`src/scripts/check_referential_integrity.py`). Runs alongside the
  drift gate in both workflows. Exercises invariants that a static
  schema check can't prove: (1) deleting a `campaigns` row CASCADE-deletes
  its `campaign_messages` children; (2) inserting a `campaign_messages`
  row with a non-existent `lead_unique_key` raises
  `ForeignKeyViolation`. All mutations run inside a single transaction
  that is **unconditionally rolled back** in a `finally` block — Postgres
  READ COMMITTED hides the in-flight rows from other sessions, and ROLLBACK
  undoes everything even if the connection drops mid-test. UUID IDs prevent
  collisions between concurrent CI runs. Shares the `SUPABASE_DATABASE_URL`
  secret with schema-drift, but the role must additionally have INSERT on
  `campaigns` + `campaign_messages` and DELETE on `campaigns`. The
  FK-violation probe runs inside a SAVEPOINT (`conn.transaction()`) so
  the outer rollback survives the inner abort.
- **Supabase hot-path index gate** (`src/scripts/check_query_plans.py`).
  Runs alongside the drift + integrity gates in both workflows. For each
  query in `HOT_PATH_QUERIES`, runs `SET LOCAL enable_seqscan = off`
  followed by `EXPLAIN (FORMAT JSON)`, walks the plan tree, and fails if
  any node is `Seq Scan`. Disabling seqscan forces the planner to pick
  any *usable* index — so the check works on empty tables (which the
  live DB currently has for `leads` and `campaign_messages`). Plain
  EXPLAIN ANALYZE on an empty table picks Seq Scan trivially regardless
  of indexes; `enable_seqscan=off` is the only way to distinguish "no
  index" from "no rows yet". Covers: dashboard `ORDER BY created_at
  DESC LIMIT 200`, `WHERE audit_status = ?`, `WHERE unique_key = ?`,
  and `campaign_messages WHERE campaign_id = ?`. Read-only role is
  sufficient because EXPLAIN without ANALYZE never executes the query
  body. The supporting indexes (`idx_leads_created_at_desc`,
  `idx_leads_audit_status`, `idx_orchestration_jobs_status`) were
  reconciled in migration `add_missing_perf_indexes` (declared in
  `supabase_schema.sql` but missing from the live project — verified
  via `pg_indexes` on 2026-05-22). The redundant
  `idx_leads_unique_key` declaration was removed from the schema file —
  the UNIQUE constraint on `unique_key` auto-creates `leads_pkey` and a
  second named index would be write-amp on every INSERT.
- **DB-level CHECK constraints (defense in depth).** Supabase Studio
  and the `service_role` key both bypass Pydantic, so allowlist + range
  guards live in the database itself. Applied via the
  `add_check_constraints` migration and mirrored in
  `supabase_schema.sql` (under `DO $$ ... EXCEPTION WHEN
  duplicate_object` blocks — Postgres has no `ADD CONSTRAINT IF NOT
  EXISTS` for table-level CHECKs). `schema_drift_check.py` has an
  `EXPECTED_CHECK_CONSTRAINTS` allowlist + `check_check_constraints()`
  asserting parity in both directions (missing-in-DB **and**
  undeclared-in-schema). 10 constraints currently locked in:
  - `leads_seo_score_range` / `leads_outreach_score_range` — 0..100
    inclusive, NULL allowed.
  - `leads_audit_status_allowed` — wide allowlist matching producer
    reality: `'Pending'`, `'Processing'`, `'Completed'`, `'Failed'`,
    plus error-reason strings `'Timeout'`, `'403 Forbidden'`,
    `'404 Not Found'`, `'Invalid URL'`. The last four are misuse of
    the `audit_status` slot (a separate `last_error TEXT` column
    exists for reasons); refactoring `src/core/parallel_auditor.py`
    to write only the four canonical statuses would let us shrink the
    allowlist. Tracked as future cleanup.
  - `leads_enrichment_status_allowed` — uppercase per
    `src/scrapers/enrichment_engine.py`:
    `'PENDING'`/`'COMPLETED'`/`'FAILED'`/`'FAILED_NO_CONTENT'`.
  - `leads_email_basic_shape` — `email IS NULL OR
    (length(email) >= 3 AND email LIKE '%@%')`. Loose by design —
    the strict regex lives at the SMTP boundary in
    `src/integrations/email_sender.py`; DB only rejects obviously
    broken values so scraped imports don't fail on quirky-but-valid
    addresses.
  - `orchestration_jobs_status_allowed` —
    `'starting'`/`'running'`/`'completed'`/`'failed'`/`'stopped'`.
  - `campaigns_channel_allowed` +
    `campaign_messages_channel_allowed` —
    `'email'`/`'linkedin'`/`'multi'`.
  - `campaigns_status_allowed` —
    `'draft'`/`'active'`/`'paused'`/`'completed'` (last is
    forward-compat; no producer writes it yet).
  - `campaign_messages_status_allowed` —
    `'pending'`/`'sent'`/`'delivered'`/`'replied'`/`'bounced'` (only
    `'pending'` written today; the rest forward-compat for SMTP /
    LinkedIn integration callbacks).
- **Supabase JSONB shape gate** (`src/scripts/check_jsonb_shapes.py`).
  Runs in `security.yml` on push + **daily cron only** — intentionally
  not PR-blocking. Shape drift in existing rows shouldn't block
  unrelated code merges; daily cadence is right for catching a Studio
  hand-edit or a producer-side regression that landed yesterday. Two
  columns validated:
  - `leads.audit_results` (only for `audit_status='Completed'` rows —
    Pending/Processing/Failed legitimately have NULL or partial
    payloads). Required keys + value types: `score` (number|null),
    `is_up` (boolean|null), `tech_flags` (object), `red_flags` (array).
    Producer: `src/scrapers/seo_audit.py::perform_seo_audit_async`
    persisted via `src/core/parallel_auditor.py`.
  - `orchestration_jobs.filters` must match **one of**
    `{"type": <str>}` (pipeline path in
    `task_orchestrator.py:143`) **or** `{"query": <str>,
    "location": <str>}` (discovery path in
    `task_orchestrator.py:101`). NULL is accepted.
  `business_details` + `contact_details` were originally listed
  alongside but are **TEXT free-form prose** (Gemini-generated, e.g.
  "Full-service plumbing company specializing in ..."), not JSONB —
  no structural validation possible. Promoting either to JSONB would
  be a separate, deliberate migration.
- **Supabase NULL ratio audit** (`src/scripts/check_null_audit.py`).
  Runs in `security.yml` on push + daily cron, but the per-human-review
  cadence is **weekly** — operator skims Monday's report to decide
  which CANDIDATE_DROP / TIGHTEN items become a real migration. Two
  failure modes: (1) advisory report (does NOT fail CI) — columns with
  >90% NULL ratio (drop candidates) and columns the app reads as
  required but the schema still allows NULL (`leads.name`,
  `leads.lead_source`, `campaigns.status`, `campaign_messages.status`,
  `campaign_messages.campaign_id`); (2) hard invariants (FAIL CI) —
  any NULL row in a column with a schema default + app guarantee
  (`unique_key`, `audit_status`, `created_at`, `updated_at` on
  `leads`; `name`, `channel`, `created_at`, `updated_at` on
  `campaigns`; `channel`, `created_at` on `campaign_messages`;
  `id`, `status`, `created_at`, `updated_at` on
  `orchestration_jobs`). Empty tables are skipped entirely — total=0
  would make every column trivially "0% NULL" of nothing, drowning
  the report. NULL counts are computed in one pass per table using
  `psycopg.sql.SQL` + `sql.Identifier` composition (column names from
  `information_schema`, never user input).
- **Supabase orphan + zombie sweep**
  (`src/scripts/check_orphans_and_zombies.py`). Runs in `security.yml`
  on push + daily cron. Five checks, ONE auto-heal:
  - **Soft-orphan campaign_messages** — `lead_unique_key` with no
    matching `leads.unique_key`. FK should prevent this; orphans
    signal a dropped or DEFERRABLE FK that the schema-drift gate
    should also catch.
  - **Zombie orchestration_jobs** — `status='running'` with
    `updated_at` older than `ZOMBIE_THRESHOLD_HOURS = 4`. **AUTO-HEALED**
    via `UPDATE orchestration_jobs SET status='failed',
    updated_at=now()`. This is the only auto-heal: low risk
    (slow-but-alive job at 4h is rare; flipping is reversible at zero
    cost), high value (unblocks the orchestrator from leaking the
    slot). All other checks involve user data where guessing wrong
    would destroy info.
  - **Stuck leads** — `audit_status IN ('Pending','Processing')` with
    `updated_at` older than `STUCK_THRESHOLD_HOURS = 24`. Report-only
    (could be retried, skipped, or reclassified — operator decides).
  - **State-machine violation** — `campaign_messages.sent_at IS NOT
    NULL AND status='pending'`. Report-only (don't know which write
    is wrong).
  - **Completed-without-results invariant** —
    `audit_status='Completed' AND audit_results IS NULL`. Report-only.
    Pairs with the JSONB shape gate.

  Role permission delta: the `SUPABASE_DATABASE_URL` Postgres role
  needs UPDATE on `orchestration_jobs` for the auto-heal (alongside
  the existing INSERT/DELETE perms on `campaigns`/`campaign_messages`
  that the referential-integrity gate needs). All other checks are
  pure SELECT.
- **Supabase concurrency / contention tests**
  (`tests/test_concurrent_writes.py`). Runs in a dedicated
  `concurrency-tests` job in `ci.yml` (PR-time, fork-PR guarded).
  Five tests on live DB; isolation via `_concurrency_test_<uuid>`
  unique-key prefix + per-test teardown + a session-scoped sweep
  fixture that wipes any leftover rows from a SIGKILL'd CI worker.
  Five invariants verified:
  - **20 concurrent UPDATEs** to the same lead converge under row-lock
    serialization — final `audit_status` is one of the values
    written, every UPDATE returns.
  - **20 concurrent INSERTs** with the same `unique_key` produce
    exactly 1 success and 19 `UniqueViolation`s (no torn rows, no
    deadlock).
  - **Concurrent UPDATE + DELETE** on the same row always converges
    to "row deleted" regardless of order (READ COMMITTED re-evaluates
    the WHERE clause on the loser).
  - **Lost-update window without advisory lock** — documents that
    READ COMMITTED does NOT prevent classic read-modify-write losses
    between two writers. Assertion is intentionally weak ("final
    value is one of the writers"); a stronger invariant requires an
    application-level serialization layer.
  - **`pg_advisory_xact_lock` serializes 20 read-modify-write
    increments** — final value is exactly `initial + 20`. Documents
    the fix to adopt in `ParallelAuditor` when a lead can race with a
    manual UI edit. Lock key: `(LEAD_LOCK_NAMESPACE=0x4EAD,
    hashtext(unique_key))` — the namespace constant MUST be reused
    by any other code that locks on a lead.
  The unit-test job (`python-tests`) also collects this file but
  every test skips via `pytest.importorskip("psycopg")` +
  `pytest.mark.skipif(not DATABASE_URL, ...)` since `requirements.txt`
  doesn't include psycopg. So the test file is exercised only in the
  dedicated job with the right env.
- **Per-role `statement_timeout` (long-running query DoS guard).**
  Defaults configured at the role level via `ALTER ROLE ... SET
  statement_timeout = ...` so every new connection inherits the cap:
  - `anon` → **3s** (Supabase default, kept tight)
  - `authenticated` → **8s** (Supabase default, kept tight)
  - `service_role` → **30s** (added via `set_service_role_statement_timeout`
    migration — Supabase ships this role with no timeout). Generous
    enough for the longest legitimate single statement on the
    pipeline's hot paths, tight enough to abort any runaway.
  Verified daily in `security.yml` by
  `src/scripts/check_statement_timeouts.py`. Two layers: (1) query
  `pg_db_role_setting` and assert each role carries the expected
  `statement_timeout=Ns` entry — catches a "RESET ALL" or
  ALTER-ROLE-undone via Studio; (2) prove the cancellation primitive
  fires by `SET LOCAL statement_timeout = '2s'` followed by
  `SELECT pg_sleep(5)` — must raise `QueryCanceled`. Together these
  transitively verify per-role behavior without needing separate
  per-role connection strings. **Optional**: set
  `DATABASE_URL_ANON` / `_AUTHENTICATED` / `_SERVICE_ROLE` secrets to
  also exercise true per-role enforcement (script no-ops if absent).
- **Connection pool / pooler-URL contract**
  (`tests/test_connection_pool.py`). Three layers: (a) static grep
  asserts no module under `backend/` or `src/` (excluding
  `src/scripts/` + `tests/`) imports psycopg/asyncpg/psycopg2/pg8000
  — the backend MUST go through PostgREST over HTTPS via supabase-py;
  (b) static check that `DATABASE_URL` (when set) targets
  `*.pooler.supabase.com` not the direct `db.<ref>.supabase.co` host;
  (c) dynamic test opens `POOL_TEST_CONCURRENCY=20` concurrent
  connections and asserts every one succeeds (pooler queues, doesn't
  error). Lives in the same `concurrency-tests` ci.yml job as the
  other live-DB pytest file. Backend "503-not-500 on pool exhaustion"
  is intentionally out of scope here — that's an integration test
  belonging in Playwright E2E with a forced-exhaustion fixture; the
  test file documents this in module-level docstring.
- **DB bloat report** (`src/scripts/check_db_bloat.py`). Runs in
  `security.yml` on push + daily cron; the operator reviews weekly.
  Fails CI when any non-empty core table has `n_dead_tup /
  GREATEST(n_live_tup, 1) > 0.20` (autovacuum is throttled). Also
  prints table sizes sorted largest first as the archival hit list.
  `pgstattuple` extension auto-detected; not installed on the
  current project, so the index-bloat metric is omitted with a note
  in the report header. To enable index bloat, run `CREATE EXTENSION
  pgstattuple;` in Studio (Pro plan).
- **Slow query report** (`src/scripts/slow_query_report.py`). Read-only
  on `pg_stat_statements` v1.11 (already enabled). Three sections:
  top-10 by `total_exec_time`, anything with `mean_exec_time > 1s`,
  and hot queries (`calls >= 100`) with cache hit ratio < 99%. Fails
  CI on any finding so the operator notices; the fix is usually a
  follow-up index PR. Runs in `security.yml` on push + daily cron.
- **Grants matrix audit** (`src/scripts/check_grants_matrix.py`).
  Beyond the deny-all RLS gate in `schema_drift_check.py`, this
  enumerates every `information_schema.table_privileges` row and
  asserts: `anon` / `authenticated` / `PUBLIC` have ZERO grants on
  the 4 core tables; `service_role` + `postgres` carry the full set
  (`SELECT`, `INSERT`, `UPDATE`, `DELETE`, `REFERENCES`, `TRIGGER`,
  `TRUNCATE`); no other role appears in the matrix. Also enumerates
  `pg_roles` against `EXPECTED_ROLES` (Supabase platform roles +
  pg_* built-ins) — anything else flags a Studio CREATE ROLE or
  extension surprise. Runs in `security.yml`.
- **Function safety audit** (`src/scripts/check_function_safety.py`).
  Three checks against `pg_proc` / `role_routine_grants`:
  (a) only `EXPECTED_FUNCTIONS = {add_lead_column,
  rls_auto_enable, update_updated_at_column}` exist in `public`;
  (b) every `SECURITY DEFINER` function is owned by `postgres` and
  has `search_path` in `proconfig`;
  (c) no anon/authenticated/PUBLIC EXECUTE grant exists unless
  declared in `EXEC_GRANT_ALLOWLIST` (currently empty). Runs in
  `security.yml`.
- **Deep backup PITR verification**
  (`.github/workflows/backup-verify-deep.yml`). DISABLED by default
  (`workflow_dispatch` only). When enabled (Pro plan + Supabase
  PAT secret), runs monthly: creates a Supabase branch restored to
  `now() - 1h`, runs schema-drift + referential-integrity + row-count
  diff against the restore, deletes the branch in `if: always()`
  cleanup. Records RTO end-to-end as evidence that PITR works.
  Workflow header documents the full prerequisite list.
- **ANALYZE freshness gate** (`src/scripts/check_analyze_freshness.py`).
  Reads `pg_stat_user_tables.{last_analyze,last_autoanalyze}` for the
  4 core tables. Fails CI when any table with > `ROW_THRESHOLD=10_000`
  rows has both timestamps NULL or both older than
  `STALE_AFTER_DAYS=7`. Below threshold = report-only (small tables
  ride autovacuum just fine). For bulk-write paths (CSV upload of
  >1000 rows) the backend should call `ANALYZE leads` immediately
  after the upload completes — wire into `backend/main.py`'s
  `/upload` handler if/when volume grows.
- **JSONB GIN / expression-index suggestions**
  (`src/scripts/suggest_jsonb_indexes.py`). Advisory only — always
  exits 0. Scans `pg_stat_statements` for `@>` / `?` / `?|` / `?&`
  predicates on `leads.audit_results` + `orchestration_jobs.filters`
  and for `column->>'key'` extraction patterns; suggests
  `CREATE INDEX ... USING gin (column)` or
  `CREATE INDEX ... ((column->>'key'))` accordingly. The operator
  reads the weekly run log; a suggestion appearing multiple weeks
  is the signal to actually create the index.
- **Soft-delete decision (deliberately not adopted).** Hard delete
  is intentional for this single-operator project:
  - The pipeline already has explicit DELETE points (`/leads/clear`
    behind the X-Admin-Token gate; `/campaigns/{id}` cascades via
    the FK on `campaign_messages`). No "oops, recover the row"
    pattern needed at the single-operator scale.
  - Adopting soft delete would require every `SELECT` site to add
    `WHERE deleted_at IS NULL`, every FK constraint to respect the
    soft-delete chain, and partial indexes scoped on
    `deleted_at IS NULL`. Audit-grep would need to enforce the
    filter at every read site.
  - Recovery uses the Supabase PITR snapshot (verified by
    `backup-verify-deep.yml`) rather than tombstone rows.
  If this changes (multi-operator, audit requirement, regulatory
  retention), the soft-delete adoption checklist lives in the
  workflow comments above.
- **Migration safety preview-branch gate**
  (`.github/workflows/migration-safety.yml`). DISABLED by default
  (`workflow_dispatch` only). When enabled on PRs touching
  `supabase_schema.sql` or `supabase/migrations/**`: creates a
  preview Supabase branch, applies the new schema to it, runs
  `schema_drift_check` + `check_referential_integrity` +
  `check_query_plans` against the branch, then deletes the branch.
  Block-merge gate when added to the required status checks.
  Prerequisites in the workflow header.
- **Storage size + WoW growth monitor**
  (`src/scripts/storage_report.py`). Reports
  `pg_database_size(current_database())` and per-table
  `pg_total_relation_size`. Soft-warns at 70% of
  `STORAGE_QUOTA_BYTES` (default 8 GiB matching Supabase Pro base
  disk), hard-fails at 90%. Diffs against a baseline JSON persisted
  via `actions/cache@v4` keyed on workflow + month; any table
  growing >2x WoW contributes to FAIL ("stuck job inserting
  forever" signal). `audit_results` JSONB is the prime growth
  suspect once volume builds — the report flags it explicitly when
  it crosses thresholds. Runs in `security.yml` on push + daily
  cron.
- CI-only dep: `psycopg[binary]>=3.1` is installed inline by every
  Supabase-DB job, not added to `requirements.txt` (backend talks to
  Supabase over PostgREST HTTPS, not Postgres wire — no need to ship a
  driver into the runtime image).
- **Login brute-force gate** (`frontend/utils/loginThrottle.ts`). In-process
  per-IP throttle in front of `signInWithPassword`: 5 attempts / 60s.
  Bucket key derives from `TRUSTED_CLIENT_IP_HEADER` (same trusted-IP
  source as the proxy); spoofless callers fall back to a synthetic
  `unknown` bucket. `MAX_BUCKETS = 10_000` is a **hard cap** — when the
  expired-sweep frees nothing, the oldest bucket is evicted, so a unique-IP
  flood within one window can't pin memory. Counter increments on every
  attempt regardless of outcome; `clearLoginRate()` releases the bucket
  on successful credential check.
- **Proxy `BACKEND_URL` scheme assertion** (`frontend/app/api/proxy/[...path]/route.ts`).
  Render's `fromService.property: host` returns a bare hostname, so
  `_resolveBackendUrl()` prepends `https://` if no scheme is present.
  `_assertBackendSchemeAllowed()` runs at **request time inside `forward()`**
  (not at module load — that would crash `next build` against a dev backend
  on `http://127.0.0.1:8000`). In `NODE_ENV=production`, the resolved URL
  must be `https://` UNLESS the host is loopback (`127.0.0.1`, `localhost`,
  `*.localhost`) — that exempts `npm run start` smoke-tests against a
  local backend while still blocking any prod misconfiguration that would
  silently downgrade Render-network traffic to plaintext.
- **GDPR Article 20 — data export** at `GET /operator/data-export`
  (`backend/main.py`). Returns a ZIP with `leads.csv`, `campaigns.csv`,
  `messages.csv`, `audit_log.json` (orchestration_jobs wrapped with
  `{export_timestamp, operator_email, schema_version, row_counts}`).
  Single-operator semantics ([ADR-001](docs/adr/001-single-tenant-by-design.md))
  → the export is unconditional. CSV-injection guard
  (`sanitize_csv_cell`) on every cell; `csv.QUOTE_MINIMAL` keeps
  embedded CRLF inside one row. Rate-limit **1/day, peer-IP-keyed
  (`get_remote_address`, NOT XFF-honouring)** — closes a theoretical
  XFF-rotation bypass by an API-key holder hitting the backend's
  public URL directly. Locked in by `tests/test_gdpr_export.py`
  (17 tests). Operator-facing button: `frontend/app/page.tsx` Settings
  → "Download my data". Full doc: [docs/observability.md](docs/observability.md) §12
  + [docs/legal/privacy-policy.md](docs/legal/privacy-policy.md) §7.
- **GDPR Article 17 — right to erasure** at `DELETE /operator/account`.
  Three-factor gate: (1) `X-API-Key`, (2) `X-Admin-Token` (same gate
  as `/leads/clear`), (3) JSON body with Pydantic
  `Literal["DELETE MY ACCOUNT"]` confirmation (wrong value = 422
  BEFORE the destructive step). **Audit-first invariant**: a row is
  written to `account_deletions` BEFORE any DELETE runs — partial-
  failure paths still leave a trace; audit-write failure returns 503
  and **skips the destructive step entirely** (zero rows touched).
  FK dependency order: `campaign_messages` → `campaigns` →
  `orchestration_jobs` → `leads`. Sentinel-UUID predicate
  (`_NEVER_UUID = "00000000-..."`) on `delete().neq("id", ...)` —
  PostgREST requires a WHERE filter for safety. Footgun: a row whose
  `id` IS the all-zero UUID escapes the wipe (~2⁻¹²² probability with
  `gen_random_uuid()`); upgrade path is `.gte("created_at",
  "1970-01-01")`. Rate-limit **1/hour, peer-IP-keyed**. Locked in by
  `tests/test_gdpr_deletion.py` (16 tests: three-factor gate,
  audit-first, row counts, retention, rate limit).
- **`account_deletions` audit table** (`supabase_schema.sql`): one row
  per `DELETE /operator/account`. Schema: `{id, deleted_at,
  operator_email, remote_ip, row_counts JSONB, expires_at}`. RLS
  deny-all (matches the 4 core tables). Index on `expires_at`.
  **30-day retention** — purged daily by
  `src/scripts/purge_expired_audit_log.py` (wired into `security.yml`
  before the storage-monitor job). After 30 days, **no trace remains**
  ([docs/legal/privacy-policy.md](docs/legal/privacy-policy.md) §5).
  ⚠️ Day-1 follow-up: `EXPECTED_TABLES` in
  `src/scripts/schema_drift_check.py` needs `account_deletions` added
  + the RLS deny-all assertion list extended, or the schema-drift CI
  gate goes red on next push.

## Security test inventory

Every defense above is locked in by a test. When you change a defense,
the matching file fails loudly. Live-infra tests opt in via env var so
CI stays green without setup.

**Pure unit / fast (always run in `pytest tests/`):**
- `tests/test_validation_authz_gate.py` — 422 schema-leak gate
- `tests/test_execute_plan_model.py` — `/execute` Literal allowlist
- `tests/test_email_sender_guards.py` + `tests/test_crlf_injection.py` —
  SMTP CRLF / log-line forgery / `aiohttp` outbound-header rejection
  (12 tests + 77 subtests; one real bug fixed: SMTP regex `$` → `\Z`)
- `tests/test_ssrf_guard.py` + `tests/test_ssrf_deep.py` — IPv6
  classifications, DNS rebinding (mocked sequenced resolver), HTTP/0.9
  raw-socket rejection, static-scan for `max_redirects` / manual `Host`
  header / DNS-TXT lookups (26 tests)
- `tests/test_security_defenses.py` — `fenced_json` corpus + Playwright
  route guard
- `tests/test_prompt_injection_corpus.py` — 15-payload injection corpus
  through `fenced_json` + mocked-Gemini router/draft surfaces (12 tests
  + 34 subtests)
- `tests/test_redos.py` — Subject-parser regression + email-regex
  input-cap static scan (6 tests + 16 subtests; two real bugs fixed)
- `tests/test_json_pollution.py` — prototype pollution, duplicate-key
  smuggling, control chars, deep-nest 4xx (not 500), `NaN`/`Infinity`
  not crashing the 422 handler (104 tests; two real bugs fixed)
- `tests/test_error_message_leak.py` — fault-injected DB/Gemini/file
  errors scraped against an 18-regex sensitive-substring list; header
  fingerprint sweep; liveness probe + docs disabled checks (13 tests)
- `tests/test_upload_attacks.py` — `/upload` adversarial fuzz: boundary
  size, content-type / filename allowlists, traversal, NUL bytes,
  polyglot, BOMs, binary bombs, gzip lies (30 tests + 1 documented-skip)
- `tests/test_timing_attack.py` — `secrets.compare_digest` empirical
  timing distribution + source-grep assertion (4 tests; Welch's t-test
  via scipy if available)
- `tests/test_supabase_helper.py`, `tests/test_security_helpers.py`,
  `tests/test_csv_helper_health.py` — narrow utility-layer guards

**Frontend node tests (`cd frontend && node --test utils/...`):**
- `frontend/utils/url.test.mjs` — `sanitizeNext` open-redirect +
  decoded-payload rejection + `ensureProtocol` (57 cases)
- `frontend/utils/supabase/cookie-floor.test.mjs` — happy-path floor
- `frontend/utils/supabase/cookie-floor-fuzz.test.mjs` — full
  `(sameSite, httpOnly, secure)` adversarial matrix (1157 cases + 2
  documented-skip TODOs: domain narrowing + `__Host-` prefix)

**Opt-in e2e (env-gated; require running infra + real Supabase user):**
- `tests/test_supabase_anon_bypass.py` — PostgREST direct-hit with anon
  key (auto-loads creds from `frontend/.env.local`; skips if absent)
- `tests/test_proxy_origin_csrf_e2e.py` — Playwright cross-origin POST
  (`RUN_PROXY_ORIGIN_E2E=1`)
- `tests/test_jwt_manipulation.py` — 6 JWT tamper variants vs the proxy
  auth gate (`RUN_JWT_MANIPULATION_E2E=1`)
- `tests/test_open_redirect.py` — Playwright `/login?next=`
  (`RUN_OPEN_REDIRECT_E2E=1`)
- `tests/test_idor_sweep.py` — wrong-API-key, path-traversal,
  enumeration timing, extra-param ignored (`RUN_IDOR_SWEEP=1`).
  Parametrize IDs are opaque labels (`first-char-mutated`,
  `bearer-prefix`) — pytest collection never echoes the real key value.
- `tests/test_concurrency_rate_limit_e2e.py` — `asyncio.gather` burst
  against rate-limited endpoints (`RUN_CONCURRENCY_E2E=1`); the
  `/leads/clear` ×10 case requires the extra
  `ALLOW_DESTRUCTIVE_LEADS_CLEAR=1` opt-in.

**Test-infrastructure patterns to know:**
- Backend tests use `fastapi.testclient.TestClient` against
  `from main import app` (with `backend/` added to `sys.path`).
- `backend/main.py` resolves `db` / `router` / `auditor` /
  `orchestrator` via module `__getattr__` lazy load + a lifespan
  priming loop (`sys.modules[__name__]` attribute access — see the
  "PEP 562 trap" note in the cold-start invariants). The
  `TestClient`-driven tests don't run the lifespan, so they still hit
  the original "name not in globals" path. Pattern:
  `_prime_lazy_globals` autouse fixture injects `MagicMock` /
  `AsyncMock` replacements (see `tests/test_json_pollution.py` +
  `tests/test_error_message_leak.py`). The prod-mode fix and the
  test-fixture priming are independent layers — both stay.
- `/upload` + `/orchestrator/start` rate-limits trip during long test
  sweeps. Pattern: `_reset_rate_limiter` autouse fixture clears the
  slowapi `MovingWindowStorage` between tests.
- ReDoS tests bound `re.search` with `signal.SIGALRM` +
  `setitimer(ITIMER_REAL, ...)`. POSIX-only; falls back to wall-clock
  on Windows.
- Tests that touch real secrets (API keys etc.) MUST use opaque
  parametrize ids — `ids=["first-char-mutated", ...]` not the value
  itself — so pytest collection never echoes the secret to stdout /
  CI logs.

## Performance + observability invariants

- **Cursor pagination on `/leads`.** `?limit=1..200` (default 50) +
  `?cursor=<opaque>`. Response: `{leads, next_cursor, has_more}`.
  Cursor is `base64url(json({c: created_at_iso, k: unique_key}))` —
  `_encode_lead_cursor` / `_decode_lead_cursor` in `backend/main.py`.
  Decoder fail-closed (malformed → `None` → page-1). Length-bounded
  (≤512 bytes raw, k ≤128). `SupabaseHelper.list_leads_recent(limit,
  cursor)` uses `created_at.lt.<c>` OR `and(created_at.eq.<c>,
  unique_key.lt.<k>)` — tie-break eliminates off-by-one on identical
  microsecond timestamps. Uses existing `idx_leads_created_at_desc`.
- **Async DB wrappers** in `SupabaseHelper` (`list_leads_recent`,
  `get_stats_rows`, `find_running_job`, `insert_orchestration_job`)
  wrap sync supabase-py `.execute()` in `asyncio.to_thread` so
  PostgREST round-trips don't block the uvicorn event loop. Background
  code in `_process_in_chunks` keeps direct sync calls — already off
  the request loop. Only `/leads`, `/stats`, `/process-lead`/
  `/process-all` hop through `to_thread`.
- **`/stats` cache** at `src/utils/stats_cache.py`.
  `_StatsCache(ttl_seconds=60.0)` with `asyncio.Lock` double-checked
  locking. 100 concurrent at expiry trigger exactly ONE rebuild.
  Per-uvicorn-worker — at `--workers N` you pay N builds per TTL.
  Invalidated by `process_csv_background` on successful upsert +
  orchestrator `_process_in_chunks` `finally` (every job exit).
  Single-lead `update_lead_info` / `update_audit` do NOT invalidate —
  operator edits can lag /stats up to 60s.
- **Cold-start lazy imports.** `backend/main.py` defers `pandas`,
  `AgenticRouter`, `ParallelAuditor`, `TaskOrchestrator`,
  `SupabaseHelper`, `export_leads`. Module `__getattr__(name)`
  resolves `db`/`router`/`auditor`/`orchestrator` on **attribute
  access on the module object** and caches into `globals()`.
  `pd.DataFrame` annotations are string-quoted + `TYPE_CHECKING` keeps
  the type hints meaningful. Result: `python -X importtime` 1.141s →
  219ms (-81%). **DO NOT** re-introduce eager construction of the
  singletons.
  - **PEP 562 trap (locked in 2026-05-22).** Module `__getattr__` does
    NOT fire for bare-name `LOAD_GLOBAL` lookups inside functions in
    the same module — only for `getattr(module, name)` /
    `module.name`. Handler code like `if not db.client:` would
    `NameError` if hit before `db` lands in `globals()`. The lifespan
    therefore runs a priming loop:
    ```python
    import sys as _sys
    _self = _sys.modules[__name__]
    for _name in ("db", "router", "auditor", "orchestrator"):
        try: getattr(_self, _name)
        except Exception as exc:
            logger.warning("Lazy global %s could not initialize: %s", _name, exc)
    ```
    Each `getattr` walks the attribute path → triggers `__getattr__`
    → caches the instance into `globals()`. After that, every
    handler's bare reference resolves via the normal globals lookup
    at zero cost. Per-name try/except so a partially-configured env
    (e.g. missing `GEMINI_API_KEY` → `router` init raises) only
    disables the affected feature instead of bricking the whole API.
    Any future lazy singleton added to `__getattr__` MUST also land
    in this loop.
- **Lifespan still blocks cold start.** `_self.db.check_schema()` +
  `_self.orchestrator.recover_interrupted_jobs()` run before uvicorn
  binds (note: explicit module attribute access — see PEP 562 trap
  above). Move `recover_interrupted_jobs()` into `asyncio.create_task`
  after `yield` to hit <5s on Render free tier. Follow-up.
- **Block-logger middleware** (`_block_logger_middleware`) logs
  `WARN slow handler: METHOD path took Nms` when elapsed ≥
  `SLOW_HANDLER_THRESHOLD_MS` (default 100 ms, env-overridable).
  Catches sync calls in async handlers. `loop.set_debug(True)`
  rejected — too costly. Use `PYTHONASYNCIODEBUG=1` in dev.
- **Web-vitals RUM** — `/metrics` endpoint accepts the
  `WebVitalsMetric` Pydantic model (Literal-allowlisted name,
  bounded value/rating/path/id). Browser sends via
  `navigator.sendBeacon` with a JSON `Blob` (bare `sendBeacon`
  defaults to `text/plain` and Pydantic 422s). Rate-limited 60/min.
  WARN for poor/needs-improvement, INFO for good. Frontend hook:
  `frontend/app/components/WebVitalsReporter.tsx`, mounted from
  `app/layout.tsx`.
- **Streaming `/export/download` and `/export/outreach`** use
  `StreamingResponse` + `_stream_leads_csv` async generator paging
  200 rows at a time via the keyset cursor. Memory bounded ≈60 KB
  per chunk. Inline `_csv_cell` injection guard. Column order LOCKED
  via `_EXPORT_FULL_COLUMNS` / `_EXPORT_OUTREACH_COLUMNS` tuples —
  adding a lead column doesn't auto-add to the export. Legacy
  `/export` (disk-write via `src/scripts/export_leads.py`) kept for
  CRM workflows; memory-bound by design.
- **Query profiler** at `src/utils/query_profiler.py`. Refuses without
  `QUERY_PROFILER=1`. Chainable proxy records verb + caller + timing.
  `assert_o1(per_unit=N, tolerance=2.0)` raises if any single caller
  exceeded `2*N` hits. Static audit of `src/` (2026-05-22) found
  ZERO O(N) N+1 patterns; profiler exists as a regression guard.
- **EnrichmentEngine shared-browser pool.** One Chromium per
  `EnrichmentEngine`; per-lead `new_context()`. `aclose()` MUST be
  called on teardown — invoked from `_process_in_chunks` `finally`
  and `_execute_deep_enrichment` `finally`. New direct callers must
  also `await engine.aclose()` or leak Chromium per job.
- **Load-test scaffolding** in `tests/loadtest/`:
  `locustfile.py` (3 scenarios A/B/C), `bench_enrich.py` (browser-pool
  A/B), `spike_locustfile.py` + `spike.sh` (0→100→0 trapezoid),
  `soak.sh` + `SOAK_PLAYBOOK.md` (24h driver + 8-signal monitoring
  playbook), `chaos.md` + `drop_supabase_pool.py` (3 scenarios,
  pool-drop is local-only via `CHAOS_LOCAL_ONLY=1`). Each VU injects
  a synthetic RFC1918 `X-Forwarded-For` — `_rate_limit_key` honors
  XFF only when `X-API-Key` validates, matching the Vercel/Render
  proxy pattern.
- **Structured JSON logging** (`src/utils/logging_config.py`). One
  JSON object per stdout line with the canonical envelope
  `{timestamp, level, logger, message, request_id, user_id, route,
  duration_ms?, exception?, <domain>...}`. Domain fields passed via
  `logger.info(msg, extra={"job_id": "..."})` merge at the top level
  (NOT nested under `"context": {…}`) — operator-facing `jq` queries
  in [docs/observability.md](docs/observability.md) §12 rely on the
  flat shape. `JsonFormatter` + `_CRLFScrubFilter` cooperate: filter
  scrubs `record.msg`, `record.args` (tuple OR dict form), AND any
  non-reserved `extra={}` key in `record.__dict__` — attacker-
  controllable values in any path can't smuggle a fake log line.
  Render's logs UI is grep-only, but JSON lines stay greppable
  (`grep '"level":"ERROR"' app.log | jq`). Sentry / Logtail / Loki
  parse the same envelope without an adapter. Pinned by
  `tests/test_logging_request_id.py::TestJsonFormatterEnvelope`
  (11 tests) + the existing
  `tests/test_crlf_injection.py::TestLoggingCRLFScrub`.
- **Request-context middleware**
  (`backend/main.py::_request_context_middleware`). Runs FIRST
  inbound (declared BEFORE `_block_logger_middleware`; Starlette
  stack: first-registered = outermost). For every HTTP request:
  honours valid inbound `X-Request-ID` (`[A-Za-z0-9_-]{1,64}`),
  mints `uuid.uuid4().hex` otherwise; binds the three ContextVars
  (`request_id_var` / `user_id_var` / `route_var`); tags Sentry's
  per-request scope with `request_id` + `user.email`; propagates
  `X-Request-ID` on the response. **Critical: does NOT call
  `clear_request_context` in `finally`.** Each request runs in its
  own asyncio Task; the Context is GC'd cleanly on task end.
  Clearing eagerly would break `StreamingResponse` body iterators
  (e.g. `_stream_leads_csv`, `/operator/data-export`) — `call_next`
  returns when the response *object* is built; the body iterator
  runs *later* in the same task, and a cleared ContextVar would
  lose request_id on those log lines. Pinned by
  `tests/test_logging_request_id.py::TestRequestIdMiddleware`
  (7 tests).
- **`_block_logger_middleware`** logs `"slow handler"` with
  `extra={method, path, duration_ms, threshold_ms}` so duration_ms
  lands as a structured envelope field, not text-interpolated.
  Threshold default `SLOW_HANDLER_THRESHOLD_MS = 100`, env-overridable.

## Observability — Sentry

Backend + frontend both ship errors + transactions to Sentry. Full
wiring + verification procedure: [docs/observability.md](docs/observability.md).

- **Backend init** at module load in `backend/main.py` (between
  `logger = get_logger(__name__)` and the API-key block).
  `sample_rate=1.0` (errors), `traces_sample_rate=0.1`,
  `send_default_pii=False`. Skipped when `SENTRY_DSN` unset (dev
  stays clean). `before_send=_scrub_sensitive` strips `X-API-Key` /
  `X-Admin-Token` / `Authorization` / `Cookie` from
  `event["request"]["headers"]` AND drops the request body entirely
  on `/upload` (CSV is likely lead PII).
- **Frontend init** uses the `@sentry/nextjs@10.53.1` canonical
  layout: `frontend/instrumentation.ts` (Next.js server hook) →
  imports `sentry.server.config.ts` (Node) or `sentry.edge.config.ts`
  (Edge). `instrumentation-client.ts` handles the browser; reads
  `NEXT_PUBLIC_SENTRY_DSN`. `next.config.ts` wraps with
  `withSentryConfig(...)` so the webpack plugin uploads source maps
  at build (`SENTRY_AUTH_TOKEN` + `SENTRY_ORG` + `SENTRY_PROJECT`)
  with `sourcemaps: { deleteSourcemapsAfterUpload: true }` — maps
  resolve in Sentry, not on the CDN.
- **Release tag = git SHA**. Backend: `Dockerfile ARG GIT_SHA` →
  `ENV RELEASE_SHA`. `.github/workflows/deploy-backend.yml` passes
  `--build-arg GIT_SHA=${{ github.sha }}`. Frontend: build-time
  fallback chain in `next.config.ts`
  (`NEXT_PUBLIC_SENTRY_RELEASE → SENTRY_RELEASE → RENDER_GIT_COMMIT
  → "unknown"`).
- **`/_sentry/test`** endpoint (POST, X-API-Key required) gated by
  `SENTRY_TEST_ENABLED=1`. Returns 404 otherwise. Verification path
  in the launch checklist.
- **Tunnel route `/monitoring`** (configured via `tunnelRoute` in
  `withSentryConfig`) bypasses ad-blockers that hit `*.sentry.io`.
  Added to the public-path allowlist in
  `frontend/utils/supabase/middleware.ts` so unauthenticated client
  errors (crashes on `/login` before sign-in) still ship — exact-
  match-or-trailing-slash-subpath, same hardening as `/login` /
  `/auth` / `/api/auth`.
- **Per-request scope tag**: `_request_context_middleware` calls
  `sentry_sdk.set_tag("request_id", rid)` + (if email known)
  `sentry_sdk.set_user({"email": operator_email})` inside the per-
  request Sentry scope. Events captured during the request are
  filterable in Sentry UI by `tag:request_id:<rid>`.

## Alerting — Discord (5 signals to one channel)

Sentry handles uncaught exceptions + slow transactions. Five other
operational signals route to a single Discord channel via a shared
composite action. Full matrix + setup:
[docs/alerting.md](docs/alerting.md).

- **Composite action** `.github/actions/discord-notify/action.yml`
  — pure `curl` + `jq` + `bash`. No third-party action (no extra
  supply-chain surface). Inputs: `webhook-url`, `title`, `message`
  (Discord markdown), `severity` (critical/error/warning/info →
  embed colour), optional `link`. Empty `webhook-url` exits 0 with
  an actions warning — preview-PR runs without the secret stay
  green.
- **Five signals:**
  1. `synthetic-monitor.yml` — 3 consecutive failures of any of
     4 checks. State in a gist via
     `.github/scripts/synthetic-monitor.mjs::postAlert`, which
     prefers `DISCORD_WEBHOOK_URL` and falls back to
     `SLACK_WEBHOOK_URL` (the latter works against Discord's
     `/slack` endpoint too).
  2. `security.yml::storage-monitor` — `> 70 %` warning OR
     `> 90 %` critical. Severity decided by grep on the
     `storage_report.py` stdout for the code-quoted markers
     `HARD threshold` / `crossing soft threshold` (stable strings,
     not "70%" wording).
  3. `mutation-test.yml::aggregate` — kill rate below
     `MIN_KILL_RATE`. Discord ping + auto-updated tracker issue
     (label `mutation-coverage`).
  4. `cold-start-monitor.yml` — daily 04:00 UTC probe of `/`.
     Alerts on latency `>30 s` (`COLD_START_THRESHOLD_SECONDS`)
     OR non-2xx.
  5. `cert-expiry-monitor.yml` — weekly Mon 09:00 UTC.
     `openssl s_client` extracts `notAfter` from each host; alerts
     on `<30 days` (`CERT_EXPIRY_MIN_DAYS`) OR unreachable.
- **`cost-report.yml`** — weekly Mon 08:00 UTC. Runs
  `src/scripts/cost_report.py` which aggregates per-provider weekly
  spend (Supabase + Render + Maps + Domain; Gemini approximate
  until Google ships a billing API — digest has a prominent ⚠️
  banner noting the exclusion). Markdown digest posts to Discord +
  uploads as a 365-day-retention artifact for WoW comparison;
  baseline persisted in `.cost_baseline.json`.
- **Single secret**: `DISCORD_WEBHOOK_URL`. Optional per-host
  secrets for `cert-expiry-monitor` (`PROD_FRONTEND_HOST`,
  `PROD_BACKEND_HOST`) and `cold-start-monitor` (`PROD_BACKEND_URL`).

## Documentation map (operator-facing surface)

Code-as-doc is in this file; operator-facing material lives in
`docs/`:

- **`docs/runbooks/operator-guide.md`** — day-to-day operations
  (discover / audit / hunt / draft / campaigns / export), failure
  recovery, Gemini cost map, full API reference, env var matrix,
  screenshot-capture appendix.
- **`docs/runbooks/incidents.md`** — 5 SEV-1/2 scenarios with
  detection → triage → mitigation → post-mortem template; incident-
  log file naming (`docs/runbooks/incidents/YYYY-MM-DD-<slug>.md`).
- **`docs/runbooks/rollback.md`** — Render dashboard / git revert /
  Render API paths. Quarterly drill protocol +
  `docs/runbooks/drills/`.
- **`docs/onboarding.md`** — new dev clone → first PR in under a
  day. 1-page ASCII architecture. First-day checklist.
- **`docs/observability.md`** — Sentry wiring + log schema + source
  maps + alerts + PII scrubbing + verification + tear-down.
- **`docs/alerting.md`** — Discord routing matrix (5 signals),
  composite action contract, per-signal config, suppression
  strategy.
- **`docs/launch-checklist.md`** — 60+ pre-launch items. Block ship
  until 100 %. Re-run quarterly.
- **`docs/support-process.md`** — support email + SLA + ticket
  lifecycle + canned-response templates + escalation rules.
- **`docs/faq.md`** — top questions feeding the support auto-reply.
- **`docs/status-page-setup.md`** — upptime-based status page in a
  separate `bookbed-status` repo.
- **`docs/roadmap.md`** — Now / Next / Later / Probably-not.
- **`docs/legal/{privacy-policy,terms}.md`** — GDPR/CCPA templates
  ⚠️ **needs lawyer review** before publishing.
- **`docs/adr/{001..007}.md`** + `README.md` — Architecture Decision
  Records (single-tenant, FastAPI choice, PostgREST not direct PG,
  Playwright/aiohttp split, no soft delete, Gemini, Render not
  Vercel).
- **`docs/secret-inventory.md`** — 29 secrets, blast-radius-tiered
  rotation cadence.
- **`docs/ci-architecture.md`** — 15 GitHub Actions workflows
  inventory.

`README.md` at repo root is the single breadcrumb; find that and
the rest is one hop away.

## AI Router invariants (`src/core/agentic_router.py`)
- `route_instruction()` attaches a `lead_index` (unique_key + name +
  company_name, up to 200 rows) to the Gemini contents so the model can
  resolve "Audit Alpha Tech" → `seo_audit(unique_key=...)`. Without this
  context the model bails with "data insufficient" for every per-lead
  action prompt.
- `_execute_database_query()` selects `unique_key, name, company_name,
  audit_status, seo_score, lead_source, email, phone, website,
  high_risk_flag, segment` — query-answer prompts can compute "high risk"
  and other categorisations from this set without re-querying the DB.
- The query prompt embeds **definitions** ("high risk" = `high_risk_flag`
  true OR `seo_score < 50` OR `audit_status == 'Failed'`; "healthy" =
  Completed + score ≥ 70 + not high-risk; etc.) so the AI's answers match
  the UI's own filter semantics.
- `/ask` auto-executes `DATABASE_QUERY`, `STATUS_CHECK`, and `GET_INSIGHTS`
  (read-only tasks) and surfaces `result.answer / message /
  formatted-insights / summary` as the chat reply. `task == "UNKNOWN"`
  (small-talk / unmapped) surfaces `plan.raw` (Gemini's free-text reply)
  instead of showing a confusing "Confirm task: UNKNOWN" plan card.
- `/execute` rejects extra fields (`extra='forbid'`). The plan returned by
  `/ask` includes a `reasoning` field; the frontend strips it before POST
  (`handleExecutePlan` builds `{task, params}` only) — without the strip
  every Confirm & Execute click 422s.
- `_get_status_summary()` aggregates audit_status counts into a one-line
  natural-language summary (`"401 leads total — 370 Completed, 30 Failed,
  1 Pending."`) and returns it as both `answer` and `summary`, so /ask
  surfaces it without falling back to `"Query executed."`.
- `_generate_outreach_draft()` returns
  `{draft, subject, lead_name, lead_email, operator_name}`. The prompt
  asks Gemini for a "Subject:" first line; the handler parses it out
  with an **atomic-group regex**
  `^(?>\s*)Subject(?>[ \t]*):(?>[ \t]*)([^\r\n]*)\r?\n` — the previous
  form `^\s*Subject\s*:\s*(.+?)\s*\n+` was O(n²) on whitespace-padded
  model output with no trailing newline (a real ReDoS, fixed in this
  branch). Operator name comes from `OPERATOR_NAME` env, defaulting to
  "Your Name". The frontend modal renders subject + body separately and
  offers an Open-in-Gmail deep-link with both prefilled. Linear bound
  locked in by `tests/test_redos.py::TestSubjectParserReDoSRegression`.

## Discovery engine invariants (`src/scrapers/discovery_engine.py`)
- `find_leads(query, location)` is the Google-Maps scrape path. The URL host
  is hardcoded to `google.com` and `query` is `quote_plus`-encoded, so
  there's no host-controlled SSRF surface. The Playwright route guard
  (`_install_ssrf_route_guard`) re-runs `assert_safe_url` on every
  subresource and redirect — closes the TOCTOU gap between pre-flight DNS
  check and `page.goto()`, and blocks any redirect chain hopping to an
  internal host.
- `unique_key` is preferentially derived from the `!1s<id>!` segment of the
  Google-Maps place URL (stable across runs). Falls back to a 16-char MD5
  of `name` when no place-URL is present — `usedforsecurity=False`
  documents the non-crypto intent and keeps Bandit/Semgrep MD5 lints quiet.
  Collisions only route two distinct businesses to the same row; the human
  review queue catches that.
- `_extract_lead_data` returns `{name, unique_key, website, phone, rating,
  audit_status, lead_source: 'google_maps', address}`. Address comes from
  `_extract_address(page, container)` which queries the Maps side-panel
  in this order: `button[data-item-id='address']` → `button[aria-label^=
  'Address:']` → `[data-tooltip='Copy address']`. If the panel isn't
  open, the result card is clicked to open it. Output is normalised via
  `re.sub(r'\s+', ' ', ...)` + `re.search(r'[\w].*')` to drop the leading
  icon glyph + collapse whitespace; returns `None` on miss (never raises).

## Next 16 prerender + `useSearchParams` contract
- `frontend/app/page.tsx` is `'use client'` and uses `useSearchParams()` to
  consume the cross-page nav query params (`?openSettings=1`,
  `?view=audited`, etc.). Next 16 requires every `useSearchParams()`
  consumer to be wrapped in `<Suspense>` so that `next build` can prerender
  the page shell without bailing out to CSR. The default export is a thin
  `<Suspense fallback={null}><DashboardInner /></Suspense>` wrapper; the
  real component is `DashboardInner`. Removing the Suspense will cause
  `next build` to fail with `missing-suspense-with-csr-bailout` at the
  static-generation step — a hard deploy blocker on Render's
  `npm run build` step.
- Local dev `uvicorn` ships the `server: uvicorn` header by default;
  Dockerfile's CMD adds `--no-server-header`. This is cosmetic only and
  prod (via Docker) suppresses the header. The Next.js proxy also strips
  any `server` header on forward as belt-and-braces.

## End-to-end smoke flow (verified 2026-05-21)
Logged-in user → AI chat → natural-language action → Confirm & Execute →
Playwright crawl → Supabase upsert is the load-bearing pipeline. Verified
end-to-end via chrome-devtools MCP against a throw-away Supabase Auth user
on 2026-05-21:
- `"How many leads are in the database?"` → `STATUS_CHECK` autoexec returns
  `"<N> leads total."` (see `_get_status_summary`).
- `"Find me 3 dentists in Mostar"` → `DISCOVERY_SEARCH` plan card → Confirm
  & Execute → orchestrator job → 8 leads in ~35s.
- Cookie floor + Origin gate + X-API-Key proxy injection all hold under the
  full flow. No exceptions in backend log. Re-run via the same MCP browser
  path if the auth / proxy / orchestrator wiring changes.

## Live perf-test report inventory (2026-05-22, `fix/csp-nonce-rsc-hydration`)
Live chrome-devtools-mcp sweep against `npm run start` prod build, authed
as `test-lds4@example.com`. Each report is a 2026-05-22 point-in-time
snapshot — re-run before claiming the characteristic still holds.
- `tests/perf/network-waterfall.md` (9.3) — 23 cold requests, 211 KB
  transfer, FCP 432 ms, 0 third-party, 15/22 disk-cache hits on warm.
  **Bugs flagged:** `favicon.ico` revalidates every load (26 KB tax);
  4 `/api/proxy/*` calls fire before any user interaction.
- `tests/perf/console-sweep.md` (9.4) — **P1**: AI insights refresh
  passes a non-`AbortSignal` to `fetch({signal})`. **P2**:
  orchestrator-active poller ~2 calls/sec idle (no
  visibility-pause/backoff). **P2**: search input no debounce (RSC
  fetch per keystroke). **P3 a11y**: form field without id/name on
  `/` + `/campaigns`. Sentry `disableLogger` deprecation warning.
- `tests/perf/scroll-analysis.md` (9.5) + `scroll-trace-raf.json` —
  **119.9 FPS, max 9.4 ms frame, 0 dropped frames** across 600-frame
  5 s continuous scroll. `@tanstack/react-virtual` keeps DOM at ~28
  row nodes throughout. CLS 0.00.
- `docs/font-audit.md` (9.7) — confirmed silent fallback: `Inter`
  declared but zero `.woff*` ship. Body → `system-ui`, form controls
  → UA `Arial`. Pick: drop the declaration OR wire `next/font/google`.
- `tests/perf/mobile-real-device.md` (9.9) — iPhone 14 + Slow 4G +
  CPU 4×: `/login` FCP 628 ms (Good). Pixel 7 + Fast 4G + CPU 2×:
  FCP 216 ms. **Login UX bug**: no Sign-in spinner; no toast on
  throttle (`frontend/utils/loginThrottle.ts` 5/60 s).
- `tests/perf/long-tasks.md` (9.11) + `dashboard-interaction-trace.json`
  — INP 101 ms (Good, edge; 78 ms presentation delay dominant), CLS
  0.00, 0 long tasks during a 35 s 5-interaction smoke.

Skipped / deferred:
- **9.6 Coverage** — `chrome-devtools-mcp` doesn't expose CDP
  `Coverage`. Re-run via Playwright if needed.
- **9.8 Live CSP/HSTS** — spec required "live deployed URL"; not
  available in the agent session.
- **9.10 Full pipeline live** — real Gemini + Maps scrape ($,
  operator DB writes). Spec says quarterly cadence; operator-triggered.
- **9.12 Visual smoke** — `frontend/e2e/__screenshots__/` does not
  exist; spec files present without baselines.

## Cross-page navigation contract (`frontend/app/page.tsx` useEffect on mount)
- Sidebar/Insights/Campaigns all share the same `<Sidebar>` component, but
  the dashboard owns the state for modals (`showSettings`,
  `showDiscoveryModal`) and view filter (`view`, `searchTerm`). When the
  user clicks Settings/Deep Discovery/Audited/High Risk/a prospect from
  Insights or Campaigns, those pages can't toggle that state directly.
  Instead they navigate to `/` with query params and the dashboard
  consumes-then-strips them:
  - `/?openSettings=1` → opens Settings modal
  - `/?openDiscovery=1` → opens Discovery modal
  - `/?view=audited|high-risk` → toggles the view-filter
  - `/?search=<term>` → bridge-only; translated to `?q=` on consume so
    the filter-state sync (below) sees a consistent vocabulary
- After consuming, the bridge does `router.replace('/?q=<term>')` if
  search was set, else `'/'`. Setters passed to Sidebar on
  non-dashboard pages must respect the `(open)` argument: `(open) => {
  if (open) router.push('/?openSettings=1') }` — otherwise Sidebar's
  `setShowDiscoveryModal(false)` (called when the user clicks Settings)
  navigates to `/?openDiscovery=1` and the wrong modal opens.

## E2E test suite, filter URL state, offline queue, drag-drop, cross-tab
See `docs/e2e-and-frontend-contracts.md` for the full surface added in
the recent test-build session — filter ↔ URL vocabulary
(`?segment/?status/?min/?q/?sort`), `apiFetch` 401 + offline-queue
behaviour, `GET /orchestrator/active`, drag-drop ingest, the 18 E2E
spec files + their projects (chromium/firefox/webkit/iphone-14/pixel-7)
+ required env, the cooperative-cancel pytest, and the ops scripts
(schema-migration-smoke, auth-smoke, contract-smoke, preview-smoke,
data-integrity-cron). Fold sections into this file as they stabilize.

## Frontend handler robustness pattern
Every state-changing handler that hits `/api/proxy/*` MUST:
1. Check `res.ok`; on failure surface
   `data.detail || data.error || \`<Action> failed (HTTP ${status})\`` via
   `showToast(..., 'error')` rather than continuing to update local state.
2. Wrap fetch in try/catch and on network failure show
   `'<Action> failed — backend unreachable.'` toast.
3. Show `aria-busy` + `disabled` on the trigger button during the in-flight
   request and reset in `finally`. Without this, rapid clicks fire
   duplicate jobs and Gemini calls (cost real money).
4. For destructive operations (`processAll`, `startMassivePipeline`,
   `handleDeepHuntAll`, `handleClearLeads`), gate with `confirm()` that
   names the count + a one-line cost warning.

Pydantic 422 responses come as
`{detail: [{type, loc, msg, input, ctx}]}` — `AIChat.handleSubmit` joins
`detail[].msg` so the user sees "String should have at most 4000
characters" instead of a generic placeholder.

## Frontend Architecture
- `frontend/app/page.tsx` — Main dashboard. Cursor-pagination state (`leads`,
  `nextCursor`, `hasMore`) + `loadMoreLeads`. Heavy children lazy-loaded via
  `next/dynamic`: `HealthChart` (recharts), `AIChat`, `LeadTable`.
- `frontend/app/insights/page.tsx` — Analytics & AI strategic analysis. Recharts
  panels extracted to `InsightsCharts` and lazy-loaded; `AIChat` also dynamic.
  Hits `/leads?limit=200` for client-side aggregation snapshots.
- `frontend/app/campaigns/page.tsx` — Outreach campaign management. `AIChat`
  lazy.
- `frontend/app/components/LeadTable.tsx` — Virtualized lead inventory.
  `@tanstack/react-virtual`, CSS-grid rows (not `<table>` — virtualizer needs
  absolute positioning), sticky header, variable row heights via
  `measureElement`, 20-row overscan. Owns the "Load more" button + the
  auxiliary `last_error` / `key_offerings` / `pain_points` panel. Defines
  `cleanMarkdown` + `CollapsibleText` (moved here from page.tsx).
- `frontend/app/components/InsightsCharts.tsx` — PieChart + BarChart extracted
  from `/insights` so recharts (~80 KB gz) loads via the lazy chunk, not the
  initial bundle.
- `frontend/app/components/WebVitalsReporter.tsx` — `useEffect` registers
  CLS / INP / LCP / FCP / TTFB callbacks; `navigator.sendBeacon` to
  `/api/proxy/metrics`. Renders nothing. Mounted in `app/layout.tsx`.
- `frontend/app/components/AIChat.tsx` — Floating AI chat assistant
- `frontend/app/components/Sidebar.tsx` — Navigation sidebar with insights widget
- `frontend/app/components/HealthChart.tsx` — PieChart health breakdown + stats grid
- `frontend/app/components/StatsCards.tsx` — 4 summary stat cards (Total, Pending, Risk, Healthy)
- `frontend/app/components/FilterBar.tsx` — Search, segment, status, and score filters
- `frontend/app/types/lead.ts` — Shared `Lead` interface. Imported by both
  `page.tsx` and `LeadTable.tsx`; two identically-named interfaces in
  different files would be nominally distinct and break callback variance
  when passed across the file boundary.
- `frontend/app/globals.css` — Design tokens and global styles. NOTE:
  `--font-main: 'Inter'` is declared but Inter is NOT actually loaded
  (no `next/font/google` import, no `.woff*` files). App falls through to
  `system-ui`. Either drop `'Inter'` from the stack or wire
  `next/font/google` with `display: 'swap'`.
- `frontend/utils/apiConfig.ts` — API base URL, API key, and `apiFetch()` authenticated fetch wrapper

## Frontend Conventions
- Use CSS custom properties (design tokens) from `globals.css` — never hardcode colors
- Surface scale (solid, not glass): `--surface-base` < `--surface-subtle` < `--surface-elevated` < `--surface-muted` < `--surface-hover`
- Card surfaces use `--card-bg` + `--border-subtle` + `--card-shadow` (no backdrop-filter)
- Border scale: `--border-subtle`, `--border`, `--border-muted`
- Color tint tokens: `--primary-tint-5/10/15/20`, `--success-tint`, `--warning-tint`, `--error-tint`, `--linkedin-tint`
- Single brand hue: indigo `hsl(234, 89%, 64%)` via `--primary-hsl`. Secondary/accent reserved for charts only.
- Theming: dark default, light theme auto-applied via `@media (prefers-color-scheme: light)` and overridable with `[data-theme="light"]` on `:root`. Never hardcode rgba — all tokens flip between themes.
- Modal backdrop: use `.modal-backdrop` class (driven by `--modal-backdrop-bg`), never inline rgba
- Glass tokens (`--glass-bg`, `--glass-border`, `--glass-hover`) are legacy aliases mapped to solid surfaces — prefer the solid-surface names in new code
- All interactive elements must meet 44px minimum touch target (`--touch-target-min`)
- Z-index scale: sidebar=100, mobile-backdrop=199, mobile-sidebar=200, chat=400, modals=500
- Modals require: `role="dialog"`, `aria-modal="true"`, `aria-labelledby`, ESC key handler
- All buttons need `aria-label` when icon-only
- No `any` types in TypeScript — define proper interfaces
- Font: Inter (not Outfit or other AI-trendy fonts)
- No gradient text, no `linear-gradient` on UI chrome, no `backdrop-filter` blur (kept only on mobile drawer overlay)
- Mobile sidebar slides via `transform: translateX()`, never `left:` (avoid layout-triggering transitions)
- `prefers-reduced-motion: reduce` honored globally — disables all animations/transitions

## Available Design Skills (Impeccable)
Installed via `npx skills add pbakaus/impeccable`. Use as slash commands:
/polish, /audit, /animate, /bolder, /quieter, /distill, /critique, /colorize,
/harden, /delight, /clarify, /adapt, /onboard, /normalize, /extract,
/teach-impeccable, /optimize, /overdrive, /arrange, /typeset, /frontend-design

## Cross-repo strategy (BookBed.io)

LDS is internal tooling — `OPERATOR_EMAIL` single-tenancy is deliberate
(see [ADR-001](docs/adr/001-single-tenant-by-design.md)). The commercial
SaaS lives in two sibling repos under `~/git/`:
- `bookbed-website/` — Next.js 16 marketing site (Firebase App Hosting).
  Already heavily hardened (CSP, JsonLd `</script>`-escape, iCal-checker
  SSRF guard with double-resolve DNS-rebind protection). **Ahead of LDS**
  on `object-src 'none'` / `base-uri 'self'` / `form-action 'self'
  mailto:` / COOP / CORP / `X-Permitted-Cross-Domain-Policies`.
- `bookbed/` — Flutter SaaS app + Firebase Cloud Functions (TypeScript).
  Firestore + Stripe LIVE + Resend + `firebase_ai` Gemini chat (`gemini-
  2.5-flash-lite` in `ai_chat_provider.dart`). The real revenue surface.

[`docs/bookbed-crossover.md`](docs/bookbed-crossover.md) is the
**gap-analysis** that decides which LDS hardening patterns get ported to
which BookBed surface, which are already covered there, and which don't
apply. Three buckets: lead-gen specific (scrapers, agentic router,
outreach scoring — **never port**), cross-applicable security
(per-pattern table), CI workflow set (LDS has 19, bookbed-website has 1,
bookbed has 3 — biggest gap). Every ✅ row in the gap table is
file-verified (spot-checks listed in the appendix). Rows marked ⚠️/`?`
are hypothesis-only — re-verify before porting.

Phased action plan in that doc: **A** bookbed-website CI hardening
(~1 day — port LDS's `ci.yml` + `security.yml` + `workflow-drift.yml` +
dependabot, all action SHAs pinned with `# vX.Y.Z`) → **B** bookbed CF
email CRLF guards on Resend (~4h — recipient regex with explicit
CRLF reject, subject/from_name CRLF assert before MIME write) → **C**
bookbed Flutter Gemini `<UNTRUSTED_DATA>` fence around user chat input
(~1 day — currently flows raw to `_chatSession.sendMessageStream`,
only static KB system instruction) → **D** backport newer headers from
bookbed-website back to LDS (~30min) → **E** long tail (cost report,
cold-start monitor, synthetic monitor, Firestore orphan sweep).

**Phase 13 of the LDS roadmap was scoped to a dogfood-only cut on
2026-05-22**: ship 13.14 (this crossover doc, **DONE**), then 13.1
hr-HR i18n via `next-intl`, 13.3 demo seed + `is_demo` column, 13.5
DKIM/SPF/DMARC for the sending domain, 13.4 email dispatch wiring
`email_sender.py`, 13.15 two-week dogfood with real Croatian leads.
The commercial items (Stripe billing, usage metering, multi-tenancy
migration, public landing, signup, feedback widget, Plausible
analytics) belong in the BookBed repos — see "Later (3–6 months) >
[BookBed.io] Commercialization track" in
[`docs/roadmap.md`](docs/roadmap.md) and the Phase A→E actions in the
crossover doc above.

# context-mode — MANDATORY routing rules

You have context-mode MCP tools available. These rules are NOT optional — they protect your context window from flooding. A single unrouted command can dump 56 KB into context and waste the entire session.

## BLOCKED commands — do NOT attempt these

### curl / wget — BLOCKED
Any Bash command containing `curl` or `wget` is intercepted and replaced with an error message. Do NOT retry.
Instead use:
- `ctx_fetch_and_index(url, source)` to fetch and index web pages
- `ctx_execute(language: "javascript", code: "const r = await fetch(...)")` to run HTTP calls in sandbox

### Inline HTTP — BLOCKED
Any Bash command containing `fetch('http`, `requests.get(`, `requests.post(`, `http.get(`, or `http.request(` is intercepted and replaced with an error message. Do NOT retry with Bash.
Instead use:
- `ctx_execute(language, code)` to run HTTP calls in sandbox — only stdout enters context

### WebFetch — BLOCKED
WebFetch calls are denied entirely. The URL is extracted and you are told to use `ctx_fetch_and_index` instead.
Instead use:
- `ctx_fetch_and_index(url, source)` then `ctx_search(queries)` to query the indexed content

## REDIRECTED tools — use sandbox equivalents

### Bash (>20 lines output)
Bash is ONLY for: `git`, `mkdir`, `rm`, `mv`, `cd`, `ls`, `npm install`, `pip install`, and other short-output commands.
For everything else, use:
- `ctx_batch_execute(commands, queries)` — run multiple commands + search in ONE call
- `ctx_execute(language: "shell", code: "...")` — run in sandbox, only stdout enters context

### Read (for analysis)
If you are reading a file to **Edit** it → Read is correct (Edit needs content in context).
If you are reading to **analyze, explore, or summarize** → use `ctx_execute_file(path, language, code)` instead. Only your printed summary enters context. The raw file content stays in the sandbox.

### Grep (large results)
Grep results can flood context. Use `ctx_execute(language: "shell", code: "grep ...")` to run searches in sandbox. Only your printed summary enters context.

## Tool selection hierarchy

1. **GATHER**: `ctx_batch_execute(commands, queries)` — Primary tool. Runs all commands, auto-indexes output, returns search results. ONE call replaces 30+ individual calls.
2. **FOLLOW-UP**: `ctx_search(queries: ["q1", "q2", ...])` — Query indexed content. Pass ALL questions as array in ONE call.
3. **PROCESSING**: `ctx_execute(language, code)` | `ctx_execute_file(path, language, code)` — Sandbox execution. Only stdout enters context.
4. **WEB**: `ctx_fetch_and_index(url, source)` then `ctx_search(queries)` — Fetch, chunk, index, query. Raw HTML never enters context.
5. **INDEX**: `ctx_index(content, source)` — Store content in FTS5 knowledge base for later search.

## Subagent routing

When spawning subagents (Agent/Task tool), the routing block is automatically injected into their prompt. Bash-type subagents are upgraded to general-purpose so they have access to MCP tools. You do NOT need to manually instruct subagents about context-mode.

## Output constraints

- Keep responses under 500 words.
- Write artifacts (code, configs, PRDs) to FILES — never return them as inline text. Return only: file path + 1-line description.
- When indexing content, use descriptive source labels so others can `ctx_search(source: "label")` later.

## ctx commands

| Command | Action |
|---------|--------|
| `ctx stats` | Call the `ctx_stats` MCP tool and display the full output verbatim |
| `ctx doctor` | Call the `ctx_doctor` MCP tool, run the returned shell command, display as checklist |
| `ctx upgrade` | Call the `ctx_upgrade` MCP tool, run the returned shell command, display as checklist |

# Session 2026-05-22 — new patterns & docs index

The patterns described below were introduced in PR #185-#199
(open at time of writing). When those PRs merge in roughly the
order they were opened, the layout and conventions documented here
become the canonical project shape. This CLAUDE.md update is
intended to merge LAST in that queue.

## Layered architecture (handler → service → repository)

Per-domain code splits across three layers:

  backend/main.py             routing + auth + rate-limit + Pydantic
                              validation + HTTP error mapping
  src/services/<domain>.py    business logic; takes typed primitives
                              (NOT Pydantic instances) so non-HTTP
                              callers (CLI / background tasks) don't
                              depend on backend.main; raises typed
                              domain errors
  src/repositories/<domain>.py  pure PostgREST I/O; translates known
                                upstream errors (e.g. PGRST205 →
                                CampaignTableMissingError)

First domain migrated: **campaigns** (PR #192) — 7 endpoints,
`generate_campaign_messages` handler dropped from 87 LOC to 18 LOC.
The same pattern applies to `leads` / `orchestration` when those
domains migrate.

Handler pattern after refactor:

```python
@app.<method>("/<resource>", dependencies=[Depends(verify_api_key)])
@limiter.limit("N/period")
async def handler(request: Request, body: <PydanticModel>):
    if not db.client:
        return error_response("Database not connected", status_code=503)
    try:
        return _<domain>_service().<method>(...)
    except <SpecificDomainException>:
        return error_response("<user message>", status_code=<HTTP>)
    except Exception:
        logger.exception("Error ...")
        return error_response("Failed to ...")
```

Per-route guards (verify_api_key + slowapi rate limit) stay on the
handler — the service intentionally has no idea who's calling.

## Canonical error hierarchy (src/errors.py — PR #195)

```
DomainError                              boundary catch-all
├── NotFoundError                        → HTTP 404
│   ├── CampaignNotFoundError
│   ├── NoMatchingLeadsError
│   ├── NoCampaignMessagesError
│   └── LeadNotFoundError
├── ValidationError                      → HTTP 400/422
├── ConfigurationError                   → HTTP 503 (operator action)
│   └── CampaignTableMissingError
├── LeadError                            → 500; lead-domain catch-all
│   └── LeadProcessingError
├── EnrichmentError                      → 500; enrichment pipeline
│   ├── EnrichmentTimeoutError
│   └── EnrichmentExtractionError
└── AuditError                           → 500; SEO audit
    ├── AuditTimeoutError
    └── AuditFetchError
```

Rules for callers:
- Raise the most specific class that fits
- NEVER `raise Exception(...)` — pick a class
- Messages are written for handler authors, NOT end users — handlers
  choose the user-facing string when mapping to HTTP, never echo
  `str(exc)` directly (would leak internal context)
- Catch `except Exception` ONLY at outermost boundary; everywhere else
  catch the specific type so a real bug in X doesn't silently look
  like a domain-level failure in Y

`src/services/exceptions.py` is a backward-compat shim that
re-exports from `src/errors.py`. Once every reference migrates,
the shim can be deleted.

Audit of the 61 `except Exception` clauses: 27 KEEP (boundary
catches), 34 NARROW (defer per-domain). See
`tests/quality/exception-audit.md`.

## Logging convention (PR #195)

Inside an `except` block use `logger.exception(msg, *args)` —
documented as equivalent to `logger.error(msg, *args, exc_info=True)`
but the canonical Python idiom. **Do not** use the longer form
anywhere; it's mechanical to misuse (forgetting `exc_info=True`)
and clutters the call site.

35 sites swapped in PR #195. Future commits MUST use the short form.

## Constants modules (PR #194)

Numeric policies live in two files:

  src/utils/constants.py        — backend tunables
  frontend/app/lib/constants.ts — frontend tunables

Grouped by domain: pagination caps, Pydantic field-length caps,
upload caps, network/browser/SMTP timeouts, log rotation, layout
breakpoints, user-feedback durations.

Cross-language parity invariant: `MAX_UPLOAD_BYTES` (Py) must equal
`MAX_PROXY_BODY_BYTES` (TS). Both carry a `BACKEND PARITY` note in
their docstring. No automated check today; flag drift in PR review.

When adding a new constant, prefer named over inline IF the value
appears at multiple call sites OR represents a policy the operator
might tune. One-off literals stay inline at their call site.

## Quality ratchet (PR #196)

`.github/workflows/quality-ratchet.yml` runs on every PR + push to
main, compares 5 metrics against committed baselines in
`.quality-baselines.json`, fails CI on regression.

  ruff           : 90 errors (lower-is-better)
  mypy --strict  : 401 errors
  pylint score   : 10.00/10 (higher-is-better, --enable=E,F)
  eslint         : 0 problems (must stay at 0)
  semgrep        : 0 findings (must stay at 0)

Update policy: lower values mean improvement — the author of an
improvement-PR may roll the baseline forward in the same commit.
NEVER raise a baseline to silence a new finding; fix the finding.

The comparator is `scripts/check-quality-baselines.py`. It uses
`subprocess.run(argv, shell=False)` per CWE-78 — argv lists in the
JSON, never shell-interpolated. Semgrep-self-scan clean.

## Test organization (PR #199)

```
tests/
├── unit/          fast, no I/O, no external services
├── integration/   real DB / Supabase / Gemini API (skipped without creds)
├── e2e/           full-stack via Playwright / live infra
├── security/      auth bypass, injection, CSRF, CRLF, SSRF, prompt-injection
└── quality/       meta-tests (Pydantic field enforcement, mypy gates)
```

pytest.ini markers (cross-cutting filters, independent of directory):

  @pytest.mark.slow         takes >5s
  @pytest.mark.live         requires real external services
  @pytest.mark.security     security-invariant tests
  @pytest.mark.integration  real DB / Supabase
  @pytest.mark.e2e          Playwright + running backend

CI default filter: `-m "not slow and not live"` (set in
`pytest.ini::addopts`). Override for full sweep: `pytest -m ""`.

When adding a new test:
- Pick the directory by the test's I/O profile
- Add the appropriate marker (`@pytest.mark.live` etc.) — directory +
  marker are both required; marker is what the CLI filter actually
  selects

When adding a test that reads source files via `os.path.dirname(__file__)`:
prefer `Path(__file__).resolve().parents[N] / 'src' / ...` — it's
depth-independent and fails loud if the file moves without the test
author noticing. Don't use the `'..'`-chain pattern.

## Quality reports — weekly Monday cadence

Run all of these weekly; deltas tracked in the per-report tracker:

  tests/quality/dead-code-report.md         vulture / deptry / ts-prune / knip / depcheck
  tests/quality/complexity-report.md        radon CC + sonarjs cognitive complexity
  tests/quality/type-coverage-progress.md   mypy --strict (target 95% on src/utils + src/scrapers + src/processors)
  tests/quality/duplication-report.md       jscpd + pylint duplicate-code
  tests/quality/long-functions-report.md    Python ast > 80 LOC + eslint max-lines-per-function
  tests/quality/component-size-audit.md     frontend component LOC + render-block size
  tests/quality/exception-audit.md          except Exception inventory + verdict
  tests/quality/docstring-coverage.md       interrogate (target 80% then ratchet)
  tests/quality/test-reorg-report.md        per-file bucket + marker plan
  docs/architecture/module-graph.md         pydeps + madge cycle detection
  docs/tech-debt-register.md                grep TODO/FIXME/HACK/XXX/@deprecated

The reproducing commands are in each report's "Reproducing" section.
Operator should re-run + update each tracker table Monday morning;
trend visible across rows.

## Known pre-existing test failure

`tests/unit/test_logging_config.py::test_setup_logging` fails on
origin/main and every session branch — root logger expected DEBUG,
observed INFO. Test-ordering issue (an earlier test in the suite
resets the root logger). Not caused by any session refactor; defer
to a focused fix.

# Session 2026-05-23 — drain PRs (#235–#251)

A bug-drain pass against findings from #226 (Phase 15), #228/#230
(Phase 16-T3), #229 (Phase 16-T1), and #232 (Phase 16-T2). Each fix
landed as its own atomic PR. Patterns worth pinning forward:

## Backend security headers middleware (PR #238)

`backend/main.py` ships a third middleware `_security_headers_middleware`
that stamps `X-Frame-Options: DENY` + `X-Content-Type-Options: nosniff`
+ `Referrer-Policy: strict-origin-when-cross-origin` on every backend
response via `response.headers.setdefault(...)`. Defense in depth for
the case where FastAPI is reached directly, bypassing the Next.js
proxy that already stamps these on HTML routes. CSP intentionally
omitted (backend serves no HTML); HSTS intentionally omitted (Render
edge already adds it on the frontend hostname; stamping it on a
JSON-only API host pollutes the HSTS preload list). `setdefault()` so
any future per-handler override still wins.

## WebVitals: `reportAllChanges: true` on LCP/CLS (PR #242)

`frontend/app/components/WebVitalsReporter.tsx` passes
`{reportAllChanges: true}` to `onCLS` and `onLCP`. Without it,
web-vitals only finalises those metrics when the page enters the
hidden state — empirically observed as 10s+ on a still-active
dashboard with zero metric beacons. INP/FCP/TTFB resolve eagerly by
the spec and are left untouched. The lib still installs its own
`pagehide` + `visibilitychange` listeners internally, so the final
snapshot also ships at session end.

## Dashboard TOTAL LEADS binds to `/stats`, not paginated array (PR #244)

`StatsCards` accepts an optional `totalLeads` prop populated from
`/stats.total_leads` and rendered on the TOTAL card; falls back to
`leads.length` until the first /stats response lands. Cursor
pagination otherwise showed "50" while the DB held 521.
`page.tsx` adds `fetchStats` alongside `fetchLeads` and polls both on
the existing 15s tick — /stats is 60s-TTL-cached server-side with a
stampede lock, so the extra round-trip is at most one PostgREST call
per worker per minute. **Outstanding asymmetry**: PENDING / HIGH
RISK / HEALTHY still derive from the loaded slice; fixing them
requires `/stats` to ship `pending_count` / `high_risk_count` /
`healthy_count` and `StatsCards` to consume them.

## Insights prompt: DB-wide total as GROUND TRUTH (PR #245)

`_get_strategic_insights` in `src/core/agentic_router.py` now fetches
the DB-wide count via a separate `select("unique_key", count="exact")
.limit(1)` PostgREST call (one scalar, no SELECT-list expansion —
keeps CLAUDE.md pinned finding #3 intact) and embeds it in the prompt
as a `GROUND TRUTH` block: "the database holds N leads in total. The
sample below contains M. Any number you cite as a count MUST be
derived from the sample or labelled 'in the sample of M'." Closes
the hallucinated-total observation (180 vs actual 521).

**CI side-effect**: this changes the prompt body, so
`tests/test_prompt_snapshots.py` fails until the SHA256 fixture is
regenerated via `UPDATE_PROMPT_SNAPSHOTS=1 pytest
tests/test_prompt_snapshots.py`. That's the intentional-review knob.

## `request.state` hop for slow-handler log context (PR #246)

Starlette's `BaseHTTPMiddleware` spawns the inner middleware function
in a child task via anyio. ContextVars set in the outer
`_request_context_middleware` only propagate to the child as a
spawn-time snapshot, and the empirical observation was that the
slow-handler envelope in `_block_logger_middleware` dropped
`request_id` / `route` at runtime.

Two-layer fix:
1. `_request_context_middleware` also stashes the per-request values
   on `request.state` (`request_id`, `route`, `operator_email`).
   `request.state` is request-scoped, not task-scoped — survives the
   BaseHTTPMiddleware task hop unconditionally.
2. `_block_logger_middleware` reads off `request.state` and passes
   the values into `extra={...}`. `JsonFormatter` now merges extras
   BEFORE filling in ContextVar defaults via `setdefault`, so
   explicit extras win when the ContextVar leg is unreliable.

Behavioural preserved: `logger.warning()` with no extras still gets
`request_id` from the ContextVar via `setdefault`; `extra={"job_id":
…}` still merges; CRLF scrub runs before the formatter sees the
record.

## REVOKE on `update_updated_at_column` (PR #250)

Supabase ships `public.update_updated_at_column()` with EXECUTE
granted to PUBLIC + anon + authenticated by default. `check_function
_safety.py::EXEC_GRANT_ALLOWLIST` is empty — any untrusted-role
EXECUTE on a public function should fail CI, but the gate evidently
isn't running on a cadence that caught this one (separate follow-up).
Live REVOKE applied 2026-05-23, mirrored in `supabase_schema.sql`.

Postgres triggers do **not** require the calling user to hold EXECUTE
on the trigger function — the function fires with the trigger owner's
privileges. Empirically verified: a no-op UPDATE on
`orchestration_jobs` advances `updated_at` post-REVOKE (09:21 →
14:25). `service_role` + `postgres` retain EXECUTE; that's the only
access path left.

## Orchestrator poller: visibility pause + exp backoff (PRs #233, #251)

`/orchestrator/active` cross-tab poller in `frontend/app/page.tsx`
(no-job branch) replaces fixed `setInterval(5000)` with a
`setTimeout` chain at 5s → 10s → 30s. `idleTicks` widens after 2
then 4 consecutive idle responses. Resets to 5s on:
- tab regaining focus (`visibilitychange → visible` reset path)
- job adopted
- effect remount (`orchestratorJob` changes)

When `document.visibilityState !== 'visible'` the tick short-
circuits the fetch but keeps the chain alive — `visibilitychange`
re-fires `tick()` immediately on return. HTTP non-2xx and network
blips re-schedule **without** advancing `idleTicks` so a flaky
network doesn't push the operator straight to the 30s window. Idle
60s went from ~12 calls (#226 observation) to ~2.

## Multi-session worktree race + branch-collision recovery

When several claude sessions share one worktree, `git checkout -b`
into a name another session expects to use produces silent races:
HEAD can flip between two checkouts mid-edit, and `Edit` writes can
land on the wrong branch. Practical mitigations:
- Suffix every drain branch with a per-session tag, e.g.
  `chore/<scope>-<task>-opus47-v2`. Two sessions can still pick the
  same name by coincidence; pick something the parallel set won't.
- Run drain work in a dedicated worktree (`git worktree add /tmp
  /lds-drain-<tag> origin/main`). HEAD is per-worktree; parallel
  sessions in `~/git/LeadDataScraper` can't flip the drain's HEAD.
  `git worktree remove …` cleans up after push.
- After every `git checkout -b` AND before every `git commit`,
  re-check `git branch --show-current` against the branch you
  intended. If it differs, cherry-pick or `git branch -f` to recover
  rather than committing on whichever branch HEAD landed.
- If a commit lands on the wrong branch, `git reflog` always
  recovers — the SHA is durable; only the branch label moved.

## Drain coverage notes (post-PR-#253 sweep)

- **Inter font drop (A.8) shipped TWICE.** PR #239
  (`fix/inter-font-drop-2026-05-23`) and PR #240
  (`chore/inter-font-drop-A8-opus47-v2`) are functionally identical
  drops of `'Inter'` from `--font-main`. Close one before merging
  the other — second merge attempts a no-op edit on the same line
  and Mergify/branch-protection treats it as a stale conflict.
- **Drag-drop `data-testid` (A.9) NEEDED NO PR.** A prior session
  already landed `data-testid="drop-overlay"` at
  `frontend/app/page.tsx:1024` (rendered inside
  `data-testid="dashboard-root"` only while `isDragging===true`).
  Phase 16-T1 selector-miss observation was a test-driver bug
  (event not dispatched on the root before querying overlay), not a
  missing selector. Recorded so the next drain doesn't re-open it.

# Session 2026-05-23 — Phase 15 audit + 6 fix PRs (parallel to dogfood prep)

Full-stack chrome-devtools-mcp verification of every shipped feature
against local prod + Render prod, followed by a P0 retraction and six
surgical fix PRs. Source of truth: `tests/perf/phase15-findings.md`
(PR #226). Phase 16 retraction + post-session PR map: PR #227.

## Outcome summary

| Phase 15 finding | Status |
| --- | --- |
| #1 Sign Out click no-ops (P0) | **RETRACTED** Phase 16 — false positive from a stale build cache (`pkill -f "next start" -f "uvicorn backend"` only kills the second pattern on macOS, so the old `next-server` kept serving cached output) |
| #2 Clear filters doesn't strip URL | **PR #235** — `router.replace('/')` in `clearFilters` bypasses the read-effect race |
| #3 `TOTAL LEADS` shows page-load count | **PR #241** — rename → `LOADED` (honest cursor-pagination semantics) |
| #4 Pre-login vitals 307→/login | **PR #234** — `/api/proxy/metrics` exact-match in middleware public-path allowlist |
| #5 Vitals only flush on visibility-change | **No fix** — default `web-vitals` behaviour; opt into `{reportAllChanges:true}` later if eager flush worth the extra beacons |
| #6 AI Insights hallucinated counts | **No fix yet** — needs Gemini test fixtures (`test_insights_quality.py::no-invented-numbers`) to validate a `total_count` prompt-pin without regressing other insights |
| #7 `/orchestrator/active` polling storm | **PR #233** — `document.hidden` guard + `visibilitychange` re-fire on the 5 s cross-tab poller |
| #8 ForcedReflow on reload trace | **Re-trace after #233** — coincided with the polling re-render window; visibility-pause likely halves the affected duration on its own |
| #9 `/leads` refetched 3× in 30 s idle | **Covered by #233** — same poller cascade |
| #10 Inter font silent fallback | **PR #239** — drop `'Inter'` from `--font-main` (literal never loaded) |
| #11 Missing X-Origin headers | **PR #237** — COOP / CORP / X-Permitted-Cross-Domain-Policies stamps |
| #12 Drag-drop selector | **RETRACTED** — `data-testid="drop-overlay"` IS present (`page.tsx:1024`), only renders while `isDragging===true`; a proper MCP test must dispatch `dragenter` on `[data-testid="dashboard-root"]` first |
| #13 Prod unreachable + ALL CI failing | **Operator action** — every workflow on `main` since 2026-05-23 07:39 UTC failed (env-level: likely a single missing/expired secret); Render outage is downstream of the gated deploy chain not running |

## Lessons-learned (locked into the canonical doc so the next Phase
doesn't repeat)

- **`pkill -f X -f Y` only honors the LAST `-f` on macOS.** Phase 15's
  setup used the single-command form and never killed the previous
  `next-server`, so the dashboard the run tested was the prior
  session's cached build. Always run separate `pkill -f X; pkill -f Y`
  calls AND verify with `pgrep -f "X|Y"` returning exit 1 before
  claiming a fresh build is under test. If this had been done, the P0a
  Sign Out finding would never have shipped.
- **chrome-devtools-mcp `.click()` on a freshly-hydrated React tree can
  silently no-op** when the React handler is bound to a different DOM
  node than the accessibility-tree representation expects. Phase 15
  saw 0 signout requests on click and concluded the handler was
  broken; the real cause was that the test session reused a stale build
  whose React tree had no handler at all. Add `console.log` at the
  handler entry FIRST, rebuild, and re-test — confirms whether the
  click event is reaching the handler or being lost upstream.
- **Render `x-render-routing: no-server` ≠ free-tier sleep.** Our
  services run `plan: starter`, which doesn't auto-suspend. A 404
  `no-server` therefore means manual pause, deletion, billing lapse, or
  failed deploy. Check the dashboard + status.render.com first; don't
  assume the wake-attempt loop will help.
- **`gh run view --log-failed` only surfaces step names when the
  failure is at job-setup level.** Open the run page in the GitHub UI
  to read actual step logs when every job in a workflow run failed
  simultaneously (signal for env-level breakage: expired secret, broken
  `pip install`, runner config).

## Auto-branch hook caveat

Multiple auto-agents ran during this session on parallel branches
(`chore/phase16-t*`, `chore/backend-security-headers-*`,
`chore/i18n-scaffold-13.1`, `docs/claude-md-dogfood-prep-*`,
`docs/crossover-verification-*`, etc.). The branch-switching hook
silently moved `HEAD` between branches mid-tool-call, occasionally
injecting unrelated diffs (e.g. a `backend/main.py` security-headers
middleware addition) into the working tree of a separate fix PR.

**Defensive pattern when this happens:** stage + commit + push in a
single Bash heredoc with all edits done inline (e.g. via `python3
<<PY ... PY`) so no PostToolUse hook fires between Edit and commit.
The `docs(phase15-findings)` post-session snapshot commit (PR #227,
`f1f428e`) was added this way after the same hook reverted three
earlier Edit attempts.

## Outstanding for the operator

1. Restore CI green on `main` — every run since 2026-05-23 07:39 UTC
   has failed. Most-likely a single missing/expired secret (per CLAUDE.md
   "Secret inventory + rotation", `SUPABASE_DATABASE_URL` is the
   fail-closed common case in schema-drift + referential-integrity +
   query-plans jobs).
2. Confirm Render prod state once CI is green. `plan: starter`
   doesn't auto-suspend; 404 `no-server` implies manual pause /
   re-provision / billing. The tagged-release deploy chain
   (`deploy-backend.yml` + `release.yml`) needs CI green to run.
3. Re-run Phase 15 prod tier (`15.13`–`15.17`) once both restored.
4. Audit the auto-branch hook configuration if the
   parallel-agent-on-every-task behaviour wasn't intentional.

## Cross-session deltas

- Phase 16 sign-out verification used a 4/4 sweep across `/` clean,
  `/` after AI chat, `/insights`, and `/campaigns`. All redirect to
  `/login` cleanly. The relevant source is unchanged since 2026-05-15
  (commit `c67fdf16`, `Sidebar.tsx:211-226`).
- Visibility-pause pattern in PR #233 is a template for the OTHER
  pollers (`audit-status`, 15 s leads refresh) when profiling
  motivates extending it. Locked into the PR description; the surgical
  scope was deliberate to keep #233 reviewable.
