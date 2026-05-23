# Synthetic monitor (heartbeat)

GitHub Actions workflow `.github/workflows/synthetic-monitor.yml` runs
every 5 minutes (best-effort cron) and exercises four lightweight
endpoints against production. Results are written to a private gist for
uptime calculation; Slack is paged on the 3rd consecutive failure and
again on recovery.

## Checks

1. `GET /` (backend, no auth) → 200, `{"status":"ok"}`
2. `GET /health/schema` with `X-API-Key` → 200, `drift == false`
3. `GET /stats` with `X-API-Key` → 200, has `total_leads`
4. `HEAD /login` (frontend, no auth) → any 2xx/3xx

The post-deploy smoke (`docs/post-deploy-smoke.md`) covers heavier
checks (`/ask` with a Gemini call, headless `/login` + JS errors, CSP
header). The heartbeat deliberately stays cheap so it can run 288×/day
without billable side-effects.

## Setup

### 1. Create a dedicated PAT

`Settings → Developer settings → Personal access tokens →
Fine-grained tokens`:

- **Resource owner**: your account
- **Repository access**: `Public Repositories (read-only)` — gist scope
  is separate from repo scope
- **Account permissions** → **Gists**: `Read and write`
- **Expiration**: 1 year (set a calendar reminder to rotate)

Copy the token. This is the value of `GIST_TOKEN`.

### 2. Create the storage gist

`gist.github.com → New gist`:

- Filename: `monitor.json`
- Content: `{"consecutive_failures":0,"alerted_at_streak":0,"results":[]}`
- **Create secret gist** (NOT public — the alert payload includes
  endpoint URLs and drift counts)

Copy the gist ID from the URL (the hex string after your username).
This is the value of `MONITOR_GIST_ID`.

### 3. Create the Slack webhook

Slack workspace → `Apps → Incoming Webhooks → Add to Slack`. Pick the
channel that should receive alerts. Copy the webhook URL. This is
`SLACK_WEBHOOK_URL`.

### 4. Add the secrets to GitHub

`Repo → Settings → Secrets and variables → Actions → New repository secret`:

| Secret               | Source                                        |
|----------------------|-----------------------------------------------|
| `PROD_BACKEND_URL`   | `https://lead-scraper-backend.onrender.com`   |
| `PROD_FRONTEND_URL`  | `https://lead-scraper-frontend.onrender.com`  |
| `PROD_API_SECRET_KEY`| matches backend `API_SECRET_KEY`              |
| `GIST_TOKEN`         | from step 1                                   |
| `MONITOR_GIST_ID`    | from step 2                                   |
| `SLACK_WEBHOOK_URL`  | from step 3                                   |

These overlap with the post-deploy-smoke workflow — the first three are
shared; the gist + Slack secrets are new.

## Alert lifecycle

- `consecutive_failures` increments on every failed run, resets to 0 on
  a passing run.
- When the counter reaches `3` AND `alerted_at_streak == 0`, the
  workflow posts a `:rotating_light:` alert to Slack and sets
  `alerted_at_streak = 3`. This prevents re-alerting every 5 minutes
  while the incident is ongoing.
- When a passing run follows a streak (`alerted_at_streak > 0`), the
  workflow posts a `:white_check_mark:` recovery message and resets
  `alerted_at_streak = 0`.

To re-arm the alert for an *escalating* incident (e.g. 1h vs 24h
escalation), extend the threshold logic in
`.github/scripts/synthetic-monitor.mjs` — the gist already carries the
streak counter.

## Uptime calculation

The gist stores the last 100 results (rolling window ~= 8h 20min at
288 runs/day, less if cron lags). For ad-hoc uptime calc:

```bash
curl -s -H "Authorization: Bearer $GIST_TOKEN" \
  https://api.github.com/gists/$MONITOR_GIST_ID \
  | jq -r '.files["monitor.json"].content' \
  | jq '[.results[] | select(.ok==true)] | length / ([.results[]] | length) * 100'
```

If you need a longer window, raise `HISTORY_CAP` in the script (the
gist file size cap is 1 MB; one result is ~250 bytes, so up to ~4000
entries fits comfortably).

## Known limitations

- **Cron lag.** GitHub Actions `schedule` is delivered on a
  best-effort basis. Under cluster load runs can lag 5–15 minutes or
  occasionally skip. The 3-strike rule absorbs single skips; alerting
  MTTD is ~15 min in steady state, longer during GHA incidents.
- **Single-vantage-point.** All checks fire from a GHA runner in
  `ubuntu-latest`'s region (US-east typically). Regional Render outages
  that don't affect that vantage point will not page. For broader
  coverage, run the workflow on a self-hosted runner in a different
  region, or pair with a third-party probe (UptimeRobot et al.).
- **Cold start = false positive risk.** Render starter dynos cold-start
  on idle. The heartbeat itself keeps the backend warm (side-benefit),
  but if you cut the heartbeat, expect the first request after idle to
  exceed the 10s per-request timeout.
- **Slack-only.** Recovery + alert messages are Slack-formatted
  (`:rotating_light:`, `:white_check_mark:`). Webhook URL is mostly
  channel-agnostic — Discord webhooks accept the same `{text}` shape —
  but emoji codes won't render in Discord. Swap to literal `🚨`/`✅` if
  you change channels.
