# API Security Invariants

Extracted from CLAUDE.md for size. Re-read fully before changing any auth, RLS, headers, rate-limit, GDPR, or security-critical code path.

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
- Destructive endpoints `DELETE /leads/clear` + `DELETE /leads/demo`
  (Phase 13.3) additionally require `X-Admin-Token` matching `ADMIN_TOKEN`
  env (defense-in-depth even if API key leaks). The Next.js proxy injects
  `X-Admin-Token` from its own server-side env for the paths in the
  `ADMIN_TOKEN_PATHS` allowlist at
  `frontend/app/api/proxy/[...path]/route.ts` (`leads/clear`, `leads/demo`
  — exact match on joined dynamic segments, so prefix collisions like
  `leads/clear-cache` can't accidentally inherit the admin token).
  Clients cannot set this header themselves; the in-browser auth gate
  (Supabase session) is the only thing that lets a user reach the proxy
  at all. Setting `ADMIN_TOKEN` in both backend `.env` AND frontend
  `.env.local` (must match) is required — without it the UI's "Clear
  All Leads" + "Remove all demo data" buttons hit 403.
- Phase 13.3 demo-data flag: `leads.is_demo BOOLEAN NOT NULL DEFAULT
  FALSE` (+ partial index `WHERE is_demo = TRUE`) seeded by
  `src/scripts/seed_demo_data.py` (20 Croatian leads, idempotent via
  `ignore_duplicates=True`, all websites + emails use `.demo.invalid`
  TLD so any accidental SSRF / SMTP probe fails at DNS). `/leads` +
  `/stats` accept `?include_demo=true|false` (default false); the
  `_compute_stats` cache only covers the default exclude-demo path —
  include-demo bypasses the cache (rare path). `agentic_router
  ._get_strategic_insights` filters `is_demo=false` unconditionally on
  both the sample SELECT and the grounding count query so AI
  recommendations never anchor on demo fixtures. Frontend "Show demo
  data" toggle in `FilterBar` persists to `localStorage['lds-include-
  demo']` (default off). Settings → Danger Zone "Remove all demo data"
  requires typing `REMOVE DEMO` (Pydantic Literal) — wrong phrase 422s
  before the handler runs; cascade order is `campaign_messages
  WHERE lead_unique_key IN (demo)` → `leads WHERE is_demo=true`.
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
