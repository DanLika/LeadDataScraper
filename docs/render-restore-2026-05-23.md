# Render service restore — 2026-05-23

Recreation of `lead-scraper-backend` and `lead-scraper-frontend` after both
services disappeared from the Render dashboard without an operator-initiated
delete. DNS subdomains remained reserved (Render edge still resolves both
hostnames) but the underlying service objects were gone.

Until either service is recreated and finishes a successful build,
production prod URLs return `000` (backend, 30 s connect timeout) and
`404` (frontend, fast no-route reply from Render edge). The CI
`post-deploy-smoke` workflow will not re-fire until a new deploy webhook
arrives, so this restoration is the only signal that prod is back.

## State found (2026-05-23)

| Surface | Probe | Result | Interpretation |
|---|---|---|---|
| Backend `GET /` | `curl --max-time 30` | HTTP `000`, 30 s timeout | Origin host unreachable; no service bound to the subdomain |
| Backend `HEAD /` | `curl --max-time 30 -I` | HTTP `000`, 30 s timeout | Same |
| Frontend `HEAD /login` | `curl --max-time 30 -I` | HTTP `404`, 0.67 s; response carries `x-render-routing: no-server` | Definitive Render-edge signal for "no service bound to hostname" (a suspended `plan: starter` service would return 503, not 404; `plan: starter` does not auto-suspend on idle in any case) |
| Frontend `HEAD /` | `curl --max-time 30 -I` | HTTP `404`, 0.25 s; same header | Same |
| DNS `lead-scraper-backend.onrender.com` | `host` | CNAME → `gcp-us-west1-1.origin.onrender.com` → Cloudflare → `216.24.57.7` | Hostname reservation intact |
| DNS `lead-scraper-frontend.onrender.com` | `host` | Same CNAME chain | Hostname reservation intact |
| Render dashboard | Operator inspection | Both services **missing entirely** from the service list | Service objects deleted, not paused |
| Operator intent | Self-report | "Nothing intentional — unexpected state" | Root cause not yet identified — see "Root cause investigation" below |

The two distinct failure shapes (timeout vs fast 404) are both consistent
with a deleted service: Render's edge handles the missing-service 404
synchronously, but the backend origin reuses a default port that times out
when no upstream is registered.

## Root cause investigation (do BEFORE recreating)

The operator reported no intentional deletion. The two plausible causes
to rule out before triggering a recreate:

1. **Billing / payment failure.** Render auto-pauses (and after extended
   non-payment, may delete) services when the account billing method
   fails. Check Render → **Account → Billing** for an unpaid invoice or
   declined card. If billing is the cause, fix payment FIRST — otherwise
   the recreated services will auto-pause again on next billing cycle.
2. **Account-level event log.** Render → **Account → Events** (or
   per-service event timeline if the service shell still appears
   anywhere) shows deletion events with actor + timestamp. If the deleter
   is a known teammate, sync with them before recreating in case there
   was an in-flight migration. If the deleter is "Render system", file a
   support ticket alongside the recreate so Render can investigate
   simultaneously.

If neither path yields a finding, recreate and treat this as a one-off
incident; file a SEV-2 incident note under `docs/runbooks/incidents/`
(see [docs/runbooks/incidents.md](runbooks/incidents.md)).

## Recovery procedure

### Step 1 — Recreate via Blueprint (Render dashboard, operator action)

1. Render dashboard → **New ▾ → Blueprint**.
2. **Connect a repository** → pick `DanLika/LeadDataScraper`.
3. Branch: `main`.
4. Render parses `render.yaml` and lists the two services it will create
   (`lead-scraper-backend`, `lead-scraper-frontend`).
5. For every env var marked `sync: false` in `render.yaml`, Render
   prompts for a value. Paste from
   [`docs/secret-inventory.md`](secret-inventory.md) — do NOT
   regenerate keys at this step unless you intend to rotate them (every
   rotation cascades into the GitHub Actions secrets + the frontend
   `.env.local` consumers).
6. Click **Apply**.

Render will:
- Build the backend Docker image (`Dockerfile` at repo root, ~5 min).
- Run `cd frontend && npm install && npm run build` for the frontend
  (~3 min on cold cache, less on warm).
- Boot both services and bind them to the same `*.onrender.com`
  hostnames the codebase + GitHub Actions secrets already reference.

### Step 2 — Env var checklist

The blueprint will prompt for these. Verify each is set before
clicking **Apply**:

#### `lead-scraper-backend`
| Key | Source | Notes |
|---|---|---|
| `SUPABASE_URL` | Supabase project → API | Same as previous |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase project → API → service_role | RLS bypass; SECRET |
| `GEMINI_API_KEY` | Google AI Studio | SECRET |
| `API_SECRET_KEY` | Local generator (e.g. `openssl rand -hex 32`) | SECRET; backend X-API-Key |
| `ADMIN_TOKEN` | Local generator | SECRET; destructive-endpoint gate |
| `ALLOWED_ORIGINS` | `https://lead-scraper-frontend.onrender.com` | Comma-separated; ALL prod origins |
| `OPERATOR_EMAIL` | Single-tenant operator email | Single-tenancy invariant |
| `OPERATOR_NAME` | Free-text signature | Appended to outreach drafts |
| `SENTRY_DSN` | Sentry project → Client Keys | SECRET; same DSN as frontend |
| `SENTRY_TEST_ENABLED` | `1` for first-boot verification, then unset | Optional |

#### `lead-scraper-frontend`
| Key | Source | Notes |
|---|---|---|
| `BACKEND_URL` | Auto-populated via `fromService` block | render.yaml does this |
| `API_SECRET_KEY` | Must match backend `API_SECRET_KEY` | SECRET |
| `ALLOWED_ORIGINS` | Same as backend | Used by proxy + signout Origin gate |
| `ADMIN_TOKEN` | Must match backend `ADMIN_TOKEN` | SECRET |
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase project → API | Public |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase project → API → anon | Public; RLS blocks data access |
| `SENTRY_DSN` | Sentry → Client Keys | SECRET (same as backend) |
| `NEXT_PUBLIC_SENTRY_DSN` | Same DSN | Public (DSNs are designed public) |
| `SENTRY_ORG` | Sentry org slug | For source map upload |
| `SENTRY_PROJECT` | Sentry project slug | For source map upload |
| `SENTRY_AUTH_TOKEN` | Sentry → User Settings → Auth Tokens | SECRET; build-time only |

The `TRUSTED_CLIENT_IP_HEADER=x-forwarded-for` env defaults via
`render.yaml` `value:` — no operator prompt.

### Step 3 — Wait for live status

Watch the Render dashboard logs panel for each service. Expected boot
markers:
- Backend: log line `Application startup complete.` (uvicorn) and
  Render's "Live" badge.
- Frontend: log line `▲ Next.js …` followed by `- Local: …` and the
  "Live" badge.

If either fails on its first build, capture the error from Render logs
and append below in **Build/deploy errors** before retrying.

## Smoke tests (post-deploy)

Run these manually once Render reports both services Live. The smoke
sequence is the same five checks the post-deploy-smoke workflow runs
automatically once webhooks are reconnected
(see [docs/post-deploy-smoke.md](post-deploy-smoke.md)).

```sh
# 1. Backend liveness
curl -sS https://lead-scraper-backend.onrender.com/ -w '\nHTTP=%{http_code}\n'
# Expect: {"status":"ok"} HTTP=200

# 2. Backend schema drift
curl -sS -H "X-API-Key: $API_SECRET_KEY" \
  https://lead-scraper-backend.onrender.com/health/schema \
  -w '\nHTTP=%{http_code}\n'
# Expect: {"drift": false, ...} HTTP=200

# 3. AI router smoke
curl -sS -X POST -H "X-API-Key: $API_SECRET_KEY" \
  -H "Content-Type: application/json" \
  -d '{"instruction":{"text":"what is my lead count"}}' \
  https://lead-scraper-backend.onrender.com/ask \
  -w '\nHTTP=%{http_code}\n'
# Expect: HTTP=200 with task / answer / message, no top-level "error"

# 4 + 5. Frontend /login + CSP header
curl -sS -I https://lead-scraper-frontend.onrender.com/login \
  -w 'HTTP=%{http_code}\n'
# Expect: HTTP/2 200, response carries Content-Security-Policy header
```

### Browser path (manual)

1. Open `https://lead-scraper-frontend.onrender.com/` — should redirect
   to `/login`.
2. Sign in with the operator Supabase Auth account.
3. Dashboard renders, lead table populated, no console errors.
4. Open Sidebar → "Settings" (modal opens), then close.
5. Type into the AI chat: `"how many leads"`. Reply within ~5 s.

Record each step's outcome in **Smoke results** below.

## Build/deploy errors

_(populate from Render logs if recreation fails the first time)_

| Service | Build step | Error excerpt | Resolution |
|---|---|---|---|
| _none_ | _none_ | _none_ | _none_ |

## Smoke results

_(populate after Step 3)_

| Check | Result | Notes |
|---|---|---|
| 1. Backend liveness | _pending_ | |
| 2. Schema drift | _pending_ | |
| 3. AI router smoke | _pending_ | |
| 4. Frontend `/login` 200 | _pending_ | |
| 5. CSP header present | _pending_ | |
| 6. Browser sign-in flow | _pending_ | |

## Follow-ups

1. **Reconnect post-deploy webhook** — the Cloudflare Worker forwarder
   described in [docs/post-deploy-smoke.md](post-deploy-smoke.md) is
   set per-service in Render → **Service → Settings → Notifications**.
   Recreated services do not retain webhooks. Re-add the forwarder URL
   + shared secret to **both** services before the next code deploy,
   otherwise the auto-rollback safety net is dark.
2. **Rotate webhook shared secret if it's stale** — see
   [docs/secret-inventory.md](secret-inventory.md).
3. **File incident note** under `docs/runbooks/incidents/` once root
   cause is understood. Include: deletion-event log line if found,
   billing status at deletion time, any teammate or sibling-session
   activity that touched the Render account.
4. **Audit Render notification settings** — Render emits "service
   deleted" events to account-owner email by default. Confirm the
   account-owner inbox is monitored; if not, set up a forwarder or add
   to the Discord alert webhook covered in
   [docs/alerting.md](alerting.md).
