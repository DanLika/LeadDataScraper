# LeadDataScraper

## Project Overview
Lead data scraping and enrichment pipeline with Supabase backend and Next.js dashboard frontend.

## Tech Stack
- **Backend**: Python, FastAPI, Supabase (database), Playwright, Google GenAI
- **Frontend**: Next.js (App Router), React 19, TypeScript, Recharts, Lucide icons

## Backend Architecture
- `backend/main.py` — FastAPI app with all API endpoints (leads, campaigns, orchestrator, AI chat, exports)
- `src/utils/supabase_helper.py` — Supabase client wrapper (uses `SUPABASE_SERVICE_ROLE_KEY` for backend ops)
- `src/scrapers/seo_audit.py` — Async SEO auditor with tech stack detection
- `src/scrapers/discovery_engine.py` — Google Maps lead discovery via Playwright
- `src/core/task_orchestrator.py` — Background job orchestration for audits, hunts, enrichment
- `src/core/agentic_router.py` — AI instruction routing (natural language → task execution)

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
  `frontend/app/login/actions.ts`. Only same-origin relative paths are accepted
  (must start with `/`, must NOT start with `//` or `/\`). The allowlist
  regex deliberately excludes `@` and `:` so a `/@evil.com/foo` value can't
  resolve to a same-origin URL whose address bar mimics the `user@host`
  phishing-display pattern. Closes open-redirect + phishing-assist on auth.
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
- Browser security headers set in `frontend/next.config.ts`: CSP
  (`script-src 'self'` in prod; `connect-src` whitelists Supabase URL + wss;
  `img-src 'self' data: blob: <SUPABASE_URL>` — no blanket `https:` so
  attacker-controlled URLs can't be rendered as tracking pixels),
  HSTS (2y + preload), `X-Frame-Options: DENY`, `X-Content-Type-Options`,
  `Referrer-Policy`, `Permissions-Policy` (camera/mic/geo off).
  `productionBrowserSourceMaps: false`.
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
  Recipient regex is `^[^@\s]+@[^@\s]+\.[^@\s]+$` — `\s` excludes `\r\n`
  so `victim@x.com\r\nBcc: attacker@evil` can't smuggle Cc/Bcc/Subject
  headers via `msg["To"]`. Subject + from_name additionally pass a
  CRLF-reject check before they are written into MIME headers — both
  carry attacker-controllable content (Gemini draft, operator override).
  When/if SMTP send wires up, this is the boundary check.
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
  `.json()` the body without SyntaxError.
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
- CI security gates in `.github/workflows/security.yml`: `pip-audit --strict`
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
  (`re.match("^Subject:..."`) before returning. Operator name comes from
  `OPERATOR_NAME` env, defaulting to "Your Name". The frontend modal
  renders subject + body separately and offers an Open-in-Gmail deep-link
  with both prefilled.

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
- **Known gap**: `_extract_lead_data` returns only `{name, unique_key,
  website, phone, rating, audit_status}` — `lead_source` and `address`
  columns end up NULL on every scraped row. Documented in BUGS.md Round 3
  (2026-05-21) with the one-line fix for `lead_source`. Until that lands,
  any cleanup / filtering query that assumes `lead_source = 'google_maps'`
  will return zero rows; use `created_at` window or `unique_key` for now.

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
  - `/?search=<term>` → pre-fills the search input
- After consuming, `router.replace('/', { scroll: false })` clears the
  query so a refresh doesn't re-trigger. Setters passed to Sidebar on
  non-dashboard pages must respect the `(open)` argument: `(open) => {
  if (open) router.push('/?openSettings=1') }` — otherwise Sidebar's
  `setShowDiscoveryModal(false)` (called when the user clicks Settings)
  navigates to `/?openDiscovery=1` and the wrong modal opens.

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
- `frontend/app/page.tsx` — Main dashboard (lead inventory, modals, orchestration)
- `frontend/app/insights/page.tsx` — Analytics & AI strategic analysis
- `frontend/app/campaigns/page.tsx` — Outreach campaign management (with sidebar + AI chat)
- `frontend/app/components/AIChat.tsx` — Floating AI chat assistant
- `frontend/app/components/Sidebar.tsx` — Navigation sidebar with insights widget
- `frontend/app/components/HealthChart.tsx` — PieChart health breakdown + stats grid
- `frontend/app/components/StatsCards.tsx` — 4 summary stat cards (Total, Pending, Risk, Healthy)
- `frontend/app/components/FilterBar.tsx` — Search, segment, status, and score filters
- `frontend/app/globals.css` — Design tokens and global styles
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
