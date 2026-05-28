# API Security — full invariants (extracted from CLAUDE.md 2026-05-26 shrink)

Long-form invariants with rationale + test pins. CLAUDE.md keeps the quick-reference; this doc keeps the why.


## Auth + transport + browser + input + SSRF + error + GDPR + frontend (L21-361)

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
- Destructive endpoints additionally require `X-Admin-Token` matching
  `ADMIN_TOKEN` env (defense-in-depth even if API key leaks). Four
  endpoints currently gate on `verify_admin_token`:
  - `DELETE /leads/clear` (Phase 13.3 — wipe all leads)
  - `DELETE /leads/demo` (Phase 13.3 — wipe demo seed rows)
  - `DELETE /operator/account` (GDPR Article 17 erasure)
  - `GET /admin/gemini-budget` (cost-cap inspection)

  The Next.js proxy injects `X-Admin-Token` from its own server-side env
  for paths in the `ADMIN_TOKEN_PATHS` allowlist at
  `frontend/app/api/proxy/[...path]/route.ts`: `leads/clear`,
  `leads/demo`, `operator/account`, `admin/gemini-budget` — exact match
  on joined dynamic segments, so prefix collisions like
  `leads/clear-cache` can't accidentally inherit the admin token.
  Aligned with backend in [PR #348](https://github.com/DanLika/LeadDataScraper/pull/348)
  after a /vibe-security audit caught the parity drift (proxy had only
  2 of 4). Clients cannot set this header themselves; the in-browser
  auth gate (Supabase session) is the only thing that lets a user reach
  the proxy at all. Setting `ADMIN_TOKEN` in both backend `.env` AND
  frontend `.env.local` (must match) is required — without it the UI's
  "Clear All Leads" + "Remove all demo data" buttons hit 403.
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

## AI clients + RLS + schema migrations + early DB gate sections (L470-557)

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

## Observability — Sentry (L1208-1250 original full)

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

## Alerting — Discord (L1252-1296 original full)

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
