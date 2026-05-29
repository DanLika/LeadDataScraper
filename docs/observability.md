# Observability — Sentry error tracking & APM

LeadDataScraper sends errors + performance traces to **Sentry** from both the
FastAPI backend and the Next.js frontend. The free tier (5k events / month)
is enough headroom for a single-operator pipeline at current volume — see
[§7](#7-cost-monitoring) for the math.

This doc tells you how to wire it, verify it, alert on it, and tear it down.

> **Status — Sentry is LIVE in prod (2026-05-29).** DSNs wired to all three
> Render services at 12:46Z; canonical tunnel handler shipped via PR #413
> at 14:14Z; round-trip event verified by Sentry-EU ingest accepting
> event_id `5ed56e453b164604b31201e7f57bf1a4` at 14:47:53Z (release tag
> `d922b334a732ed1a62f86bd5136d856119c3ea4c`). Projects:
> `lds-backend` (project_id `4511473178574928`) + `lds-frontend`
> (project_id `4511473196925008`). Org `o4511473167695873`, EU region
> (`.ingest.de.sentry.io`). Source-map upload (`SENTRY_AUTH_TOKEN`) still
> unwired — frontend stack traces stay minified until the operator
> populates it. Memory: `sentry_enabled_2026-05-29.md` +
> `sentry_tunnel_415_fix_2026-05-29.md`.

## At a glance

| Component | Init location | DSN env var | Default sample rates |
|---|---|---|---|
| **Backend (FastAPI)** | `backend/main.py` — module-level block, runs before `app = FastAPI(...)` | `SENTRY_DSN` | errors 100%, traces 10% |
| **Frontend server (Node runtime)** | `frontend/sentry.server.config.ts` via `frontend/instrumentation.ts::register` | `SENTRY_DSN` | errors 100%, traces 10% |
| **Frontend edge** | `frontend/sentry.edge.config.ts` via `frontend/instrumentation.ts::register` | `SENTRY_DSN` | errors 100%, traces 10% |
| **Frontend browser** | `frontend/instrumentation-client.ts` (Sentry v8+ canonical location) | `NEXT_PUBLIC_SENTRY_DSN` | errors 100%, traces 10% |

> ⚠️ **Deviation from the original spec**: step 4 ("Init in `app/layout.tsx`")
> was implemented via the `@sentry/nextjs` standard pattern — config files at
> the project root + `withSentryConfig` wrap in `next.config.ts`. The
> alternative (literal init inside `app/layout.tsx`) cannot upload source
> maps at build time (`@sentry/nextjs`'s webpack plugin only hooks in via
> `withSentryConfig`). Source maps were a hard requirement of step 5, so
> the standard pattern is the only one that satisfies both.

## 1. One-time install

### 1a. Backend Python dep

`sentry-sdk[fastapi]>=2.20,<3` is now in `requirements.in` (range pin — the
exact version + sha256 hashes get locked into `requirements.txt` on the
next regen). Regenerate the lockfile:

```bash
make lock-python   # regenerates requirements.txt with sha256 hashes
git add requirements.in requirements.txt
```

> The `ci.yml::lockfile-sync` gate will fail until you commit both files
> together — see [`docs/onboarding.md`](onboarding.md) §4d.

### 1b. Frontend Node dep

`@sentry/nextjs@10.53.1` is now in `frontend/package.json` (verified
current stable as of 2026-05-22; exact pin per the dependency-pinning
policy for security-relevant libs). Install:

```bash
cd frontend
npm install
```

This populates `node_modules/@sentry/nextjs` and resolves the TypeScript
diagnostics that show "Cannot find module '@sentry/nextjs'" until the
install runs.

### 1c. Browser init is deferred post-FCP (PR #419, `a55149b3`)

`frontend/instrumentation-client.ts` does NOT eager-import `@sentry/nextjs`.
The SDK is loaded via a dynamic `import()` inside `requestIdleCallback`
(with a `setTimeout(100ms)` fallback for browsers without rIC, mainly
Safari ≤ 16). The 454 KB raw / 142 KB gz SDK chunk no longer ships in
`rootMainFiles`; it lives in a deferred async chunk that streams in after
first contentful paint.

Why the wrapper export:

```ts
import type * as SentryNs from '@sentry/nextjs';
type RouterTransitionFn = typeof SentryNs.captureRouterTransitionStart;

let routerTransitionHandler: RouterTransitionFn = () => {};

export const onRouterTransitionStart: RouterTransitionFn = (...args) =>
  routerTransitionHandler(...args);
```

Next 16's framework picks up `onRouterTransitionStart` at module-eval; it
must be a synchronous module-level export. The wrapper above is a stable
no-op that delegates to a mutable handler — the handler stays a no-op
until the deferred SDK chunk loads and rebinds it.

**Tradeoff.** Errors thrown in the ~50–200 ms between page load and the
idle-callback fire are NOT captured. Hydration errors typically surface
after init anyway, so this has been acceptable for LDS's single-operator
workload. If pre-init error capture is ever needed, the canonical
revert is to delete §1c's import wrapping and restore the eager
`import * as Sentry from '@sentry/nextjs'` at the top of
`instrumentation-client.ts`.

**Measured impact** (`next 16.2.6` Turbopack, prod `/login`, gzip -9):

| Metric                          | Baseline | Post-defer | Delta             |
|---------------------------------|----------|------------|-------------------|
| `rootMainFiles` TOTAL gz        | 206 019  | 133 707    | −72 312 (−35 %)   |
| Sentry SDK gz in `rootMainFiles`| 142 282  | 0          | −142 282 (−100 %) |
| Sentry SDK gz (deferred async)  | —        | 180 168    | +180 168 lazy     |
| Lighthouse `/login` LCP median  | 1502 ms  | 1336 ms    | −166 ms (−11 %)   |
| Lighthouse `/login` Perf median | 100      | 100        | 0                 |

Deferred chunk grew 142 → 180 KB gz because
`integrations: defaults => defaults.filter(i => i.name !== 'BrowserTracing')`
is a runtime filter — it removes the integration's executor at SDK init,
but the integration code itself remains inside the SDK barrel. True
tree-shake would need `defaultIntegrations: false` + an explicit
curated `integrations: [...]` list AND avoiding the `@sentry/nextjs`
barrel — not done.

Memory: `sentry_defer_post_fcp_2026-05-29.md`. Bundle baseline:
`bundle_audit_2026-05-29.md`.

## 2. Create a Sentry project

1. Sign in at <https://sentry.io>.
2. **Create Project** → choose **FastAPI** (this gives the backend DSN).
3. **Create Project** → choose **Next.js** (this gives the frontend DSN).
4. Either project's DSN looks like
   `https://<key>@o<org-id>.ingest.us.sentry.io/<project-id>`.

> 💡 The two projects share the same Sentry org, so dashboards and alerts
> can be team-scoped if you want a single view. Single-operator can use one
> org, two projects, no team.

You'll also need:

- **`SENTRY_ORG`** — the org slug from the Sentry URL.
- **`SENTRY_PROJECT`** — the frontend project's slug (for source-map upload
  from `next build`).
- **`SENTRY_AUTH_TOKEN`** — Sentry → Settings → Auth Tokens. Scope:
  `project:releases`, `project:write`. Treat as a secret.

## 3. Set environment variables

### 3a. Backend `.env` (local dev)

**Append to your existing `.env`** (the one you created in
[onboarding §2a](onboarding.md#2-environment-variables)) — don't re-copy
from `.env.example`, which would clobber your `ADMIN_TOKEN` and any other
values you already set.

```bash
cat >> .env <<'EOF'

# Sentry (optional in dev — leave SENTRY_DSN unset to disable)
SENTRY_DSN=
SENTRY_ENVIRONMENT=development
# Set to 1 only during verification — exposes POST /_sentry/test
SENTRY_TEST_ENABLED=
EOF
```

If `SENTRY_DSN` is empty/unset, the backend skips `sentry_sdk.init()`
entirely. **Dev without Sentry is safe** — no DSN, no events, no exception
noise on import.

### 3b. Frontend `.env.local` (local dev)

**Append to your existing `frontend/.env.local`** — same warning as
backend, don't recreate from scratch.

```bash
cat >> frontend/.env.local <<'EOF'

# Sentry (optional in dev — leave both unset to disable)
SENTRY_DSN=
NEXT_PUBLIC_SENTRY_DSN=
SENTRY_ENVIRONMENT=development
NEXT_PUBLIC_SENTRY_ENVIRONMENT=development
EOF
```

`SENTRY_ORG`, `SENTRY_PROJECT`, `SENTRY_AUTH_TOKEN` are **build-time only** —
needed if you want source-map upload from your local `npm run build`. For
local `npm run dev`, leave them unset.

### 3c. Render dashboard (production)

`render.yaml` declares the env-var schema. **Set the actual values** in the
Render dashboard for each service:

**Backend service:**
| Env var | Value |
|---|---|
| `SENTRY_DSN` | Backend project DSN |
| `SENTRY_ENVIRONMENT` | `production` (already declared) |
| `SENTRY_TEST_ENABLED` | leave unset normally; set to `1` only during verification |

**Frontend service:**
| Env var | Value |
|---|---|
| `SENTRY_DSN` | Frontend project DSN |
| `NEXT_PUBLIC_SENTRY_DSN` | Same value as `SENTRY_DSN` |
| `SENTRY_ORG` | Sentry org slug |
| `SENTRY_PROJECT` | Frontend project slug |
| `SENTRY_AUTH_TOKEN` | Token from Sentry Settings → Auth Tokens |

`SENTRY_ENVIRONMENT` + `NEXT_PUBLIC_SENTRY_ENVIRONMENT` default to
`production` in `render.yaml` (no manual entry needed).

`SENTRY_RELEASE` / `NEXT_PUBLIC_SENTRY_RELEASE` are auto-resolved at build
time via the fallback chain in `frontend/next.config.ts`:

```
NEXT_PUBLIC_SENTRY_RELEASE → SENTRY_RELEASE → RENDER_GIT_COMMIT → "unknown"
```

Render exposes `RENDER_GIT_COMMIT` on every build, so the release name is
automatic. **No manual env var needed** — unless you want a semver-tagged
release like `1.4.0` instead of the bare commit SHA, in which case set
`SENTRY_RELEASE` explicitly.

## 4. Release tagging (source maps)

Stack traces in Sentry resolve to your source code only if **release name +
source maps** are uploaded together. The pipeline handles both:

- **Backend**: `Dockerfile` declares `ARG GIT_SHA=unknown` and exports it as
  `ENV RELEASE_SHA`. The `deploy-backend.yml` workflow passes
  `--build-arg GIT_SHA=${{ github.sha }}`, so every prod image is labeled
  with the exact commit. The backend reads `RELEASE_SHA` in `sentry_sdk.init`.
  **Python doesn't ship source maps** — Sentry resolves frames against the
  git commit directly. No upload step.
- **Frontend**: `withSentryConfig` in `next.config.ts` runs the Sentry
  webpack plugin during `next build`. The plugin:
  1. Uploads source maps to Sentry using `SENTRY_AUTH_TOKEN` /
     `SENTRY_ORG` / `SENTRY_PROJECT`.
  2. Sets the release name from `release: { name: SENTRY_RELEASE }`.
  3. Hides source maps from the public bundle (`hideSourceMaps: true`) so
     they only exist in Sentry, not on the CDN.

> If `SENTRY_AUTH_TOKEN` is missing in Render's build env, source-map upload
> is silently skipped and stack traces stay minified in Sentry. Symptom:
> Sentry shows `e:32:1234` instead of `Component.tsx:48`. Fix: set the
> token, redeploy.

## 5. Verify the integration (target: < 60s round-trip)

Step 6 of the original spec — confirm an error reaches Sentry from a deploy.

### 5a. Backend verification (`/_sentry/test`)

A purpose-built endpoint hides behind `SENTRY_TEST_ENABLED=1`. Returns 404
in normal operation.

```bash
# 1. In Render dashboard → backend service → Environment, set
#    SENTRY_TEST_ENABLED=1. Save → Redeploy.

# 2. Trigger:
curl -X POST "https://<your-frontend>/api/proxy/_sentry/test" \
  -b "<session cookie>"
# Backend should respond with a 500 (the test handler raises a RuntimeError).

# 3. Wait 30–60 s. Open the backend Sentry project's "Issues" tab.
#    You should see a fresh issue titled:
#    "Sentry verification test triggered — if you see this in Sentry, the integration works."

# 4. Unset SENTRY_TEST_ENABLED in Render. Redeploy. /_sentry/test now 404s.
```

### 5b. Frontend verification (browser)

DevTools console on a logged-in dashboard page:

```javascript
// Throw a labeled error that bubbles to the React error boundary
throw new Error("Sentry frontend verification — " + new Date().toISOString());
```

Then wait 30–60 s and refresh the frontend Sentry project's Issues tab. You
should see the error grouped with stack frames resolving to
`page.tsx:<line>` (proof that source maps uploaded).

### 5c. Frontend verification (server-side)

Force a server-side error via an obviously malformed proxy call (the
Next.js route handler will throw → instrumentation.ts captures via
`onRequestError`):

```bash
curl -X POST "https://<your-frontend>/api/proxy/" \
  -b "<session cookie>"   # missing path segment causes a 500
```

## 6. Slack / email alerts for new fingerprints

Sentry's UI handles this — no code change needed.

### 6a. Slack (recommended)

1. Sentry → **Settings** → **Integrations** → search "Slack" → **Add to
   Project**.
2. Authorize Slack workspace; pick a channel (e.g. `#leadscraper-errors`).
3. Sentry → **Alerts** → **Create Alert** → **Issue Alert**.
4. **When**: "An issue is first seen" (= new fingerprint, the alert you
   actually want).
5. **If**: `level >= error` (skip info-level breadcrumbs).
6. **Then**: "Send a Slack notification to ..." → pick the channel.
7. Save. Repeat for each project (backend + frontend).

### 6b. Email (fallback)

If you don't want Slack, the same Alert rule with **Then: "Send an email to
team members"** notifies every member of the project's team. For a
single-operator setup with one user, this delivers to your Sentry account
email.

### 6c. Suggested additional alerts

- **Spike alert**: "Error count > 50 in 1 hour" → Slack. Catches a
  regression that produces many duplicates of the same fingerprint.
- **Performance alert**: "Transaction p95 > 5 s for /process-all" → Slack.
  Catches a slow chain regression (Playwright hang, Supabase pooler
  saturation).
- **Crash-free session rate** (frontend only): "Crash-free sessions < 98%"
  → email. Conservative threshold for a single-operator UI.

Documented in: Sentry → **Alerts** tab → existing rules visible there.

## 7. Cost monitoring

Sentry free tier:

| Resource | Free tier ceiling | Estimated monthly usage |
|---|---|---|
| Errors (events) | 5 000 | ~50–200 (single operator) |
| Performance transactions | 10 000 | ~3 000 (at 10% sampling) |
| Replays | 50 | 0 (replays not enabled) |
| Attachments | 1 GB | 0 |

Math for the transaction estimate: Render's `synthetic-monitor.yml` keeps
the backend warm hourly (24 × 30 = 720 synthetic hits/month). The operator
fires perhaps 50 real-traffic actions/day × 30 = 1 500/month. Frontend
page loads cap at maybe 10/day × 30 = 300. Total ~2 500 transactions. At
10% sampling: ~250 sampled.

If you blow the budget anyway, the dial is in `sentry_sdk.init` /
`Sentry.init({ tracesSampleRate })`. Drop to `0.05` (5%) and redeploy.

> If sustained error volume exceeds 5k/month, you have a different problem
> than a Sentry budget — *something is broken in your pipeline.* Fix the
> bug; don't reduce the sample rate.

## 8. PII scrubbing

`send_default_pii=False` is set on both backend and frontend `Sentry.init`.
Beyond that, custom `before_send` hooks scrub:

| Source | Scrubbed | Where |
|---|---|---|
| `X-API-Key` header | `[scrubbed]` | backend `_scrub_sensitive`, frontend `sentry.server.config.ts::beforeSend` |
| `X-Admin-Token` header | `[scrubbed]` | same |
| `Authorization` header | `[scrubbed]` | same |
| `Cookie` header | `[scrubbed]` | same (Supabase session JWT, must never leak) |
| `POST /upload` request body | dropped entirely | backend only (CSV PII) |

**Lead data in errors** is currently *not* scrubbed by default. If a
handler raises with a lead-name in the message, Sentry sees it. That's
acceptable for the single-operator setup — the operator is the only person
who reads Sentry. If you ever route Sentry to a wider audience, extend the
`before_send` hook to redact `name` / `email` / `phone` / `company_name`
from error contexts.

## 9. Tear-down (disable Sentry entirely)

Both runtimes are DSN-gated. To disable Sentry without removing the code:

- **Backend**: unset `SENTRY_DSN` in Render. Redeploy. `sentry_sdk.init` is
  skipped (the `if _SENTRY_DSN:` guard); error capture is off but the app
  runs identically.
- **Frontend**: unset `SENTRY_DSN` + `NEXT_PUBLIC_SENTRY_DSN`. Redeploy.

To remove permanently:

1. Revert the edits in `backend/main.py`, `frontend/next.config.ts`, and
   `requirements.in` / `frontend/package.json`.
2. Delete `frontend/instrumentation.ts`, `frontend/instrumentation-client.ts`,
   `frontend/sentry.server.config.ts`, `frontend/sentry.edge.config.ts`.
3. Run `make lock-python` and `cd frontend && npm install` to drop the
   transitive deps.
4. Remove the SENTRY_* env keys from `render.yaml`.

## 10. The `/monitoring` tunnel route

`withSentryConfig` is configured with `tunnelRoute: "/monitoring"`. At
build time, Sentry's webpack plugin creates a same-origin Next.js route at
`/monitoring` that forwards client SDK beacons to `*.ingest.sentry.io`.
Two reasons it exists:

1. **Ad-blocker bypass.** Many blocklists target `*.sentry.io` directly.
   A same-origin proxy is invisible to those rules.
2. **CSP simplicity.** `connect-src 'self'` covers the tunnel; no need to
   add Sentry's ingest URL to the per-request CSP in `frontend/proxy.ts`.

The auth middleware (`frontend/utils/supabase/middleware.ts`) explicitly
**public-allowlists `/monitoring`** so unauthenticated callers can still
ship beacons. This matters because the most useful errors to capture —
crashes on the `/login` route itself — happen BEFORE a session exists. If
`/monitoring` required auth, those events would be 302'd to `/login` and
lost.

Boundary is tight: only `/monitoring` (and any sub-path) is exempt, not
arbitrary unauthenticated routes. The `isPublicPrefix` helper uses
exact-match or trailing-slash-subpath logic (the same hardening that
covers `/login`, `/auth`, `/api/auth`).

### 10.1 Manual handler at `app/monitoring/route.ts`

The webpack-plugin virtual route was empirically returning 404 in prod
(see `test-results/10-mobile.md` RESP-044 — `GET /monitoring` and `POST
/monitoring` both 404 against `lead-scraper-frontend.onrender.com`,
confirmed via direct HTTP probe 2026-05-28). Root cause unconfirmed
(suspected build-step glitch in `@sentry/nextjs@10.53.1` + Next 16
interaction). Belt-and-braces fix: a physical route handler at
`frontend/app/monitoring/route.ts` that always resolves in Next 16 App
Router regardless of plugin auto-injection state. The physical file
takes precedence over any webpack-injected virtual handler at the same
path.

`Sentry.init({ tunnel: '/monitoring' })` is set explicitly in
`instrumentation-client.ts` (in addition to the `tunnelRoute` build-time
injection) so the client SDK uses the tunnel even if the build-time env
shim drops the value.

> **Implementation rewritten 2026-05-29 (PR #413, commit `4366ece9`)**:
> the route now delegates to `@sentry/core`'s canonical `handleTunnelRequest`.
> The original bespoke handler rejected every real envelope with HTTP 415
> because (1) Sentry's tunnel transport sends `text/plain;charset=UTF-8`
> (deliberate — skips CORS preflight), not `application/x-sentry-envelope`;
> and (2) the upstream URL was built without `?sentry_key=…` auth query
> params that ingest requires. Verified end-to-end at 2026-05-29T14:47:53Z:
> Sentry ingest accepted event_id `5ed56e453b164604b31201e7f57bf1a4` from a
> browser-triggered test error. Memory: `sentry_tunnel_415_fix_2026-05-29.md`.

Handler behaviour (`POST /monitoring`):

| Condition | Response |
|---|---|
| `NEXT_PUBLIC_SENTRY_DSN` unset at build time | `204 No Content` (silent no-op — keeps dev/preview clean) |
| `Content-Length` > 1 MB | `413 Payload Too Large` (fast-path DoS guard) |
| Body fails envelope-header JSON parse | `400 Bad Request` (delegated to `handleTunnelRequest`) |
| Envelope `dsn` ≠ `NEXT_PUBLIC_SENTRY_DSN` | `403 Forbidden` (anti-SSRF — `allowedDsns` list mismatch) |
| Upstream Sentry ingest reject | upstream Response forwarded verbatim |
| Otherwise (success) | upstream Response forwarded verbatim — body is Sentry's `{"id":"<event_id>"}` echo + `X-Sentry-Rate-Limits` / `Retry-After` headers passthrough so the SDK can throttle |
| OPTIONS preflight | `204 No Content` |

Note: **no Content-Type check.** Canonical handler ignores incoming
Content-Type entirely; envelope validity is gated by the first-line JSON
header parse + DSN allowlist match. This is intentional — the previous
strict `application/x-sentry-envelope` allowlist was the root cause of
the multi-day silent telemetry drop.

Runtime is `edge` (Web APIs only — `Uint8Array`, `Response`, `fetch`) and
`dynamic = 'force-dynamic'`.

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Backend init logs `Sentry initialized (release=unknown, env=production)` | `RELEASE_SHA` not set on the image | Dockerfile builds: pass `--build-arg GIT_SHA=$(git rev-parse HEAD)` or rely on the deploy-backend.yml workflow |
| Frontend Sentry shows minified stack traces (`e:32:1234`) | Source maps didn't upload | `SENTRY_AUTH_TOKEN` missing in Render frontend build env — set it and redeploy |
| `Cannot find module '@sentry/nextjs'` in IDE | `npm install` hasn't run yet | `cd frontend && npm install` |
| `/_sentry/test` returns 404 | `SENTRY_TEST_ENABLED` not set | Set to `1` in Render → save → redeploy |
| Sentry shows 0 events after a known error | DSN wrong, or network blocked Sentry's ingest | Check DSN in Render dashboard; check `/monitoring` tunnel route reachable from browser |
| `POST /monitoring` returns `404` in prod | Sentry webpack-plugin virtual route handler not generated for this build | §10.1 — `frontend/app/monitoring/route.ts` is the physical fallback; if it's missing for any reason, restore from `fix/sentry-tunnel-route` |
| `POST /monitoring` returns `415` | Route handler is the pre-#413 bespoke version with the strict Content-Type allowlist | Pull `frontend/app/monitoring/route.ts` to `4366ece9` or later. Canonical impl delegates to `@sentry/core`'s `handleTunnelRequest` and accepts the `text/plain;charset=UTF-8` envelopes that Sentry actually sends. |
| `POST /monitoring` returns `403` | Envelope's embedded DSN doesn't match `NEXT_PUBLIC_SENTRY_DSN` | DSN env-var drift between Render frontend service and the Sentry project — re-sync |
| Slack alert never fires | Alert rule's project filter doesn't match | Sentry → Alerts → edit rule, confirm `project` filter matches the backend/frontend project name |
| `sentry-sdk` import fails after `make lock-python` | Lockfile didn't regenerate | `pip install pip-tools && make lock-python && pip install --require-hashes -r requirements.txt` |

## 12. Structured logs (JSON, request_id, the canonical schema)

Backend logs are emitted as **one JSON object per line** to stdout (Render
captures it) and optionally to a rotating file (`LOG_FILE` env). Each line
carries the canonical envelope plus any domain fields the caller passed
via `logger.info(msg, extra={"...": "..."})`.

### Schema

```json
{
  "timestamp":   "2026-05-22T14:30:15.123Z",
  "level":       "INFO",
  "logger":      "backend.main",
  "message":     "Lead Data Scraper Backend Starting...",
  "request_id":  "ab12cd34ef56...",
  "user_id":     null,
  "route":       "/process-all",
  "duration_ms": 142.7,
  "method":      "POST",
  "job_id":      "..."
}
```

| Field | Source | Notes |
|---|---|---|
| `timestamp` | `record.created` UTC | ISO 8601, millisecond precision, `Z`-suffix |
| `level` | `record.levelname` | DEBUG / INFO / WARNING / ERROR / CRITICAL |
| `logger` | `record.name` | usually `backend.main`, `src.core.task_orchestrator`, etc. |
| `message` | `record.getMessage()` | after `%`-arg interpolation; CRLF-scrubbed |
| `request_id` | ContextVar set by `_request_context_middleware` | `null` for module-init / lifespan / background lines until they bind their own |
| `user_id` | ContextVar set when proxy forwards `X-Operator-Email` (future) | `null` today — the Next.js proxy doesn't forward operator email yet |
| `route` | `request.url.path` at request entry | literal path, not the matched route pattern |
| `duration_ms` | populated by `_block_logger_middleware` on slow requests | absent (i.e. not set as a key) on normal lines unless caller passes it via `extra` |
| `<domain>` | `extra={...}` passed by caller | merged at the top level; e.g. `job_id`, `lead_unique_key`, `chunk_index` |
| `exception` | populated when `exc_info=True` (or `logger.exception`) | full traceback string |

### request_id middleware

`_request_context_middleware` in `backend/main.py` runs first on inbound
(declared before `_block_logger_middleware`). For every HTTP request it:

1. **Reads** `X-Request-ID` from the inbound headers. If 1–64 chars,
   `[A-Za-z0-9_-]` only, it's honoured verbatim. Otherwise a fresh
   `uuid.uuid4().hex` (32 hex chars, no dashes — tighter grep) is
   minted.
2. **Reads** `X-Operator-Email` if present (proxy doesn't forward today;
   future change).
3. **Binds** `(request_id, user_id, route)` to the three ContextVars
   via `bind_request_context()`. Every log line within the handler's
   asyncio task inherits these — the formatter reads the ContextVars
   inside `format()`.
4. **Tags Sentry's per-request scope** with `request_id` (so events
   captured during the request are filterable in Sentry by
   `tag:request_id:<rid>`) and `user.email` when an operator email is
   available.
5. **Propagates** the ID on the response as `X-Request-ID` so the
   Next.js proxy / curl / DevTools can correlate.
6. **Clears** the ContextVars in `finally` to prevent state leaking to
   coroutines sharing the asyncio task.

### Greppable examples

```bash
# All ERROR lines on a single deploy
docker logs lead-scraper-backend 2>&1 | jq -c 'select(.level=="ERROR")'

# Every line tied to one failed request (after grabbing the X-Request-ID
# from a curl response header)
docker logs lead-scraper-backend 2>&1 \
  | jq -c 'select(.request_id=="ab12cd34ef56...")'

# Slow-handler audit for a path
docker logs lead-scraper-backend 2>&1 \
  | jq -c 'select(.message=="slow handler" and .path=="/process-all") | {timestamp, duration_ms}'
```

### Dispatch tick telemetry (Issue #367 / PR #371)

Every cron invocation of `src/workers/dispatch_tick.run_tick` emits a
single `dispatch_tick summary` INFO line on EVERY return path —
including early exits like `db_client_unavailable`, `runtime_cap_after_sweep`,
`dispatcher_unavailable`, `batch_fetch_failed`. Operator no longer
needs a wrapper script to see what happened in a given tick.

The summary log carries `result.as_dict()` via `extra={}` — every
`TickResult` field is a top-level structured field on the log
record.

**Counter buckets — orthogonal, never merged:**

| Field | What it counts |
|-------|----------------|
| `claimed` | Rows transitioned `pending → dispatching` this tick |
| `dispatched` | `push_leads.success_count` (rows successfully sent) |
| `failed` | Pre-dispatch rejects (no_email / missing_step / no_variants / render error) + provider-reported `push_leads.failed_count` |
| `swallowed` | Per-lead `push_result.errors[]` rows we couldn't reconcile to a claimed `message_id`. Row stays in `'dispatching'`; sweeper resets after `claim_timeout_min`. **Operator action**: investigate dispatcher contract drift |
| `skipped_suppressed` | Address landed on suppression between schedule and tick |
| `skipped_window` | Out-of-window OR `PriorMessageNotReadyError` (+1h bump) |
| `swept_stale` | Stale `'dispatching'` rows reset to `'pending'` at top of tick |
| `errors[]` | Tick-level error tags: `dispatch_failed:<exc>`, `push_leads_unmatched:<code>`, `mark_send_failed_noop:<code>`, `db_client_unavailable`, `runtime_cap_after_*`, etc. |

**Per-lead error fan-out (`_handle_per_lead_errors`):**

For every `push_result.errors[]` entry, the function ALWAYS logs the
raw error at INFO BEFORE reconciliation, so operators see every
failure regardless of whether the email → `message_id` lookup
succeeds. Four reconciliation outcomes, each with a distinct log
level:

| Outcome | Log level | `errors[]` entry | `swallowed` |
|---------|-----------|------------------|-------------|
| Matched email → `mark_send_failed` succeeds | INFO `dispatch_tick marked msg=… bounced (send_failed)` | none | 0 |
| Single-batch fallback (1 claim + 1 err, email mismatch) | WARNING `single-message fallback assigns to …` | none | 0 |
| Unmatched multi-batch (orphan claim) | WARNING `unknown email; cannot reconcile` | `push_leads_unmatched:<code>` | +1 |
| `mark_send_failed.matched=False` (row state changed mid-tick) | WARNING `mark_send_failed matched 0 rows for msg=…` | `mark_send_failed_noop:<code>` | 0 |

**Greppable recipes:**

```bash
# Every tick summary on prod (Render log stream)
docker logs lead-scraper-backend 2>&1 \
  | jq -c 'select(.message=="dispatch_tick summary") | {timestamp, claimed, dispatched, failed, swallowed, elapsed_seconds}'

# Find ticks with orphan claims (operator action item)
docker logs lead-scraper-backend 2>&1 \
  | jq -c 'select(.message=="dispatch_tick summary" and .swallowed > 0)'

# Raw per-lead errors for one cron tick (use the timestamp window from the summary)
docker logs lead-scraper-backend 2>&1 \
  | jq -c 'select(.message=="dispatch_tick push_leads per-lead error") | {timestamp, err_email, error_code, error_message}'

# All ticks that hit a transport-level dispatcher exception
docker logs lead-scraper-backend 2>&1 \
  | jq -c 'select(.message=="dispatch_tick summary" and (.errors[]? | startswith("dispatch_failed:")))'
```

The `mark_send_failed` repo method predicate is `status='dispatching'`
(PR #369), not `status='pending'` — without that fix, every per-lead
reconciliation matched zero rows and the row stranded until the stale-claim
sweeper reset it ~15 min later.

### Domain field convention

Pass attacker-controllable values via the **args path**
(`logger.info("%s", lead_name)`) or the **extra path**
(`extra={"lead_unique_key": uk}`) — the CRLF scrub filter covers both.
Avoid:

```python
# DON'T — eager concat; less ergonomic, same security as args
logger.info("processed " + lead_name)
```

```python
# DO — args path
logger.info("processed %s", lead_name)
# DO — extra path with structured field
logger.info("processed lead", extra={"lead_unique_key": uk})
```

### Binding context from background tasks

For background work (orchestrator chunks, post-deploy probes, cron
handlers) that wants logs to correlate to its parent job, bind a
synthetic request_id manually:

```python
from src.utils.logging_config import bind_request_context, clear_request_context

tokens = bind_request_context(f"job-{job_id}", user_id=None, route="orchestrator")
try:
    await do_chunk_work(...)
finally:
    clear_request_context(tokens)
```

Pattern is the same as the middleware uses — pair the bind with a
finally so state doesn't leak.

### Output transports

| Sink | When | Configured by |
|---|---|---|
| `stdout` | always | `StreamHandler` added by `setup_logging()` |
| Rotating file `LOG_FILE` | when env set | `RotatingFileHandler`, 10 MB × 5 backups |
| Sentry events | on errors + sampled transactions | `sentry_sdk.init` (§3); SDK consumes the same logger via its `LoggingIntegration` default |

Render's logs UI reads stdout. The file sink is optional — useful for
local `tail -f` workflows or shipping to a downstream tool that watches
a file.

## 13. What Sentry sees vs. what stays local

| Signal | Sentry | Logs (stdout, `logging_config.py`) | Frontend WebVitals (`/metrics`) |
|---|---|---|---|
| Uncaught backend exception | ✅ event + stack + transaction | ✅ ERROR line with traceback | — |
| Slow handler (>100 ms) | ⚠️ as transaction if sampled | ✅ WARN line via `_block_logger_middleware` | — |
| `/upload` 4xx (bad CSV) | ⚠️ as breadcrumb only (operator-actionable) | ✅ INFO line | — |
| Frontend uncaught error | ✅ event + stack + source-mapped | — | — |
| LCP / CLS / INP degradation | — | ✅ via `/metrics` | ✅ ingested + logged |
| Rate-limit trip | ❌ (slowapi's 429 isn't an error) | ✅ INFO line | — |

Sentry is the **uncaught-exception** + **slow-transaction** signal. For
RUM (CLS / INP / LCP), keep using the existing `/metrics` endpoint +
`WebVitalsReporter` — that's free, locally aggregable, and has its own
budget. Don't route web-vitals into Sentry; they cost the transaction
budget without buying anything the existing pipeline can't compute.

## References

- `backend/main.py` — Sentry init block + `/_sentry/test` endpoint
- `frontend/instrumentation.ts` — Next.js standard server hook
- `frontend/sentry.server.config.ts` — Node runtime init
- `frontend/sentry.edge.config.ts` — Edge runtime init
- `frontend/instrumentation-client.ts` — Browser init
- `frontend/next.config.ts` — `withSentryConfig` wrap + release fallback chain
- `Dockerfile` — `ARG GIT_SHA` → `ENV RELEASE_SHA`
- `.github/workflows/deploy-backend.yml` — `build-args: GIT_SHA=${{ github.sha }}`
- `render.yaml` — `SENTRY_*` env declarations
- Sentry docs:
  - Python FastAPI: <https://docs.sentry.io/platforms/python/integrations/fastapi/>
  - Next.js: <https://docs.sentry.io/platforms/javascript/guides/nextjs/>
