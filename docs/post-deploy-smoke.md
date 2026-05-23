# Post-deploy smoke + auto-rollback

GitHub Actions workflow `.github/workflows/post-deploy-smoke.yml` runs five
checks against the production backend + frontend after every Render deploy
and rolls back the backend service to the previous live deploy on any
failure.

## Checks

The workflow runs two scripts in sequence:

### Pre-step — `dependency-health.mjs` (non-blocking)

Probes hard externals BEFORE the code smoke so a Gemini/Supabase outage
doesn't trigger a needless rollback of an otherwise-clean deploy.

1. Gemini `generateContent` 5-token completion → 200, non-empty
2. Supabase REST root `/rest/v1/` with anon key → 200
3. Supabase `/rest/v1/leads?select=unique_key&limit=1` with service_role → 200
4. `HEAD https://www.google.com/maps` → 2xx/3xx
5. Render API `GET /v1/services/{id}` → 200

Failure here posts a `:warning:` Slack message naming the degraded dep
and **suppresses the rollback step** for the smoke run (smoke still
runs; rollback decision gated on `deps.outcome == 'success'`).

### Code smoke — `post-deploy-smoke.mjs` (rollback trigger)

1. `GET /` (backend) → 200, body `{"status":"ok"}`
2. `GET /health/schema` with `X-API-Key` → 200, `drift == false`
3. `POST /ask` with `{"instruction":{"text":"what's my lead count"}}` → 200
   with a usable result (`task` / `answer` / `message`, no `error`)
4. `GET /login` (frontend) loads in headless Chromium, no `pageerror` and
   no `console.error`
5. `Content-Security-Policy` response header present on `/login`

## Trigger path

Render does not call GitHub's `repository_dispatch` API directly. Wire it
up via any tiny forwarder. Minimal Cloudflare Worker that does the job:

```js
// Cloudflare Worker — environment vars: GH_TOKEN (repo:dispatch scope),
// GH_OWNER, GH_REPO, SHARED_SECRET (matches Render webhook secret).
export default {
  async fetch(req, env) {
    if (req.method !== 'POST') return new Response('method', { status: 405 });
    if (req.headers.get('x-render-secret') !== env.SHARED_SECRET) {
      return new Response('unauthorized', { status: 401 });
    }
    const payload = await req.json();
    // Render's deploy notification includes deploy.id, service.id, etc.
    // Only forward succeeded deploys — failed deploys never went live, so
    // there's nothing to smoke-test or roll back.
    if (payload?.type !== 'deploy_succeeded') return new Response('ignored');

    const body = {
      event_type: 'render-deploy',
      client_payload: {
        new_deploy_id: payload.data.deploy.id,
        backend_service_id: payload.data.service.id,
      },
    };
    const r = await fetch(
      `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/dispatches`,
      {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${env.GH_TOKEN}`,
          'Accept': 'application/vnd.github+json',
          'Content-Type': 'application/json',
          'User-Agent': 'render-to-gha-forwarder',
        },
        body: JSON.stringify(body),
      },
    );
    return new Response(`gh=${r.status}`, { status: r.ok ? 200 : 502 });
  },
};
```

In Render → **Backend service → Settings → Notifications → Add Webhook**,
point at the Worker URL and set the same `SHARED_SECRET` header (Render
custom-header field).

## GitHub secrets

Set in **Settings → Secrets and variables → Actions**:

| Secret                  | Value                                             |
|-------------------------|---------------------------------------------------|
| `PROD_BACKEND_URL`      | `https://lead-scraper-backend.onrender.com`       |
| `PROD_FRONTEND_URL`     | `https://lead-scraper-frontend.onrender.com`      |
| `PROD_API_SECRET_KEY`   | matches backend `API_SECRET_KEY` env              |
| `RENDER_API_KEY`        | Render dashboard → Account → API Keys             |
| `GEMINI_API_KEY`        | matches backend `GEMINI_API_KEY` env              |
| `SUPABASE_URL`          | matches backend `SUPABASE_URL` env                |
| `SUPABASE_ANON_KEY`     | Supabase project → API → `anon` public key        |
| `SUPABASE_SERVICE_ROLE_KEY` | matches backend `SUPABASE_SERVICE_ROLE_KEY`   |
| `SLACK_WEBHOOK_URL`     | reused from synthetic-monitor (see that doc)      |

The forwarder supplies `backend_service_id` + `new_deploy_id` at dispatch
time so the workflow only needs the URLs and credentials as repo secrets.

## Manual re-run

`Actions → post-deploy-smoke → Run workflow`. Supply
`backend_service_id` and `new_deploy_id` from the Render dashboard.

## Rollback behavior

On smoke failure, the workflow lists the 20 most-recent deploys via
`GET /v1/services/{id}/deploys`, picks the newest `live` deploy that
isn't the one we just tested, and posts to
`POST /v1/services/{id}/rollback` with that id. Render then redeploys
the previous image — same observable effect as a manual rollback from
the dashboard, no rebuild required.

Frontend is not rolled back automatically — `/ask` and `/health/schema`
test backend health; CSP + `/login` checks only confirm the frontend
*responded*. If a frontend regression slips through, roll back the
frontend service manually from the Render dashboard.

## Cost note

Check 3 (`POST /ask`) routes through `AgenticRouter`, which means **one
Gemini API call per deploy**. With Render's typical push-driven deploy
cadence this is negligible, but it does show up on the Gemini bill. If
deploy frequency spikes (e.g. a release-train branch), swap the smoke
prompt for a router task that doesn't hit Gemini, or drop check 3
entirely and rely on `/health/schema` + manual `/ask` validation.

## Rate-limit note

`/ask` is rate-limited 10/min by `slowapi` keyed on caller IP. Smoke
fires once per deploy from a GitHub Actions runner IP, so collisions
with operator traffic are unlikely. Manual re-runs in quick succession
may 429 the third attempt within a minute.

## Forwarder payload caveat

The Cloudflare Worker snippet reads `payload.data.deploy.id`,
`payload.data.service.id`, and gates on `payload.type ===
'deploy_succeeded'`. These field paths reflect Render's documented
webhook schema at the time of writing; confirm against the current
Render docs ("Webhook Events") before pointing the Worker at prod. If
the shape has drifted, the first real deploy will silently no-op
(Worker returns 200, no `repository_dispatch` is fired, no smoke runs).
