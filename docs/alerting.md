# Alerting — one Discord channel, five signals

LeadDataScraper has five operational signals worth a real-time ping. They
all funnel into **one Discord channel** via a shared composite action. One
URL, one secret, one place to mute when the operator is asleep.

Sentry handles uncaught exceptions and slow transactions; see
[`docs/observability.md`](observability.md). This doc covers everything
else.

## Why Discord, not Slack / PagerDuty / email

| Channel | Free tier | Why not |
|---|---|---|
| **Discord** ✅ | Webhooks unlimited; history unlimited | Picked |
| Slack | 90-day message retention on free tier; per-app webhook quota | History wipes; can't grep last quarter |
| PagerDuty | 5 users free, 14-day retention | Overkill; single-operator never needs paging vs. notification |
| Email | Free | Inboxes already noisy; alert fatigue inevitable |

Single-operator + bounded alert volume means Discord wins on cost +
ergonomics. A future move to PagerDuty needs only a new composite-action
target.

---

## Signal → routing matrix

| Signal | Source workflow | Trigger | Severity | Discord title |
|---|---|---|---|---|
| **Synthetic monitor** | `synthetic-monitor.yml` | 3 consecutive failures of any of 4 checks (`/`, `/health/schema`, `/stats`, `/login`) | `error` | `🚨 Synthetic monitor: 3 consecutive failures` (and recovery: `✅ Recovered`) |
| **Storage** | `security.yml::storage-monitor` | `> 70 %` of plan quota OR `> 90 %` hard fail | `warning` (70 %) / `critical` (90 %) | `💾 Supabase storage > 70 % / 90 %` |
| **Mutation kill rate** | `mutation-test.yml::aggregate` | Any target below `MIN_KILL_RATE` (default 80 %) | `warning` | `🧬 Mutation kill rate below 80 %` |
| **Cold start** | `cold-start-monitor.yml` | Daily 04:00 UTC probe of `/` takes `> 30 s` OR non-2xx | `warning` | `🐌 Cold-start / latency probe failed` |
| **Cert expiry** | `cert-expiry-monitor.yml` | Weekly Mon 09:00 UTC check finds any host `< 30` days from expiry | `warning` | `🔒 TLS cert expiry imminent` |

All five share the same composite action — `.github/actions/discord-notify`
— and the same secret, `DISCORD_WEBHOOK_URL`.

---

## One-time Discord setup

1. **Create the server / pick an existing one.** A solo-operator
   single-server Discord is fine — make a channel called `#alerts` (or
   `#ldscraper-alerts` if multi-project).

2. **Create a webhook.** Server Settings → Integrations → Webhooks →
   **New Webhook**. Name it `LeadDataScraper Alerts`. Pick the channel.
   Copy the webhook URL.

   The URL looks like
   `https://discord.com/api/webhooks/<id>/<token>` — treat as a secret.
   Anyone with this URL can post arbitrary content to the channel.

3. **Add to GitHub repo secrets.** Repo Settings → Secrets and variables
   → Actions → New repository secret:

   ```
   Name:  DISCORD_WEBHOOK_URL
   Value: https://discord.com/api/webhooks/<id>/<token>
   ```

4. **Synthetic monitor: nothing extra to do.** The
   `.github/scripts/synthetic-monitor.mjs` reads `DISCORD_WEBHOOK_URL`
   directly (preferred) and falls back to `SLACK_WEBHOOK_URL`. As long
   as `DISCORD_WEBHOOK_URL` is set per step 3, the 3-fail streak +
   recovery messages route into Discord automatically with native
   embeds matching the composite action's colours.

   **Legacy `SLACK_WEBHOOK_URL` migration (optional).** If you'd
   rather not maintain a Discord secret separately, you can repoint
   the existing Slack secret at Discord's Slack-compatible endpoint:

   ```
   Old: https://hooks.slack.com/services/T.../B.../...        (Slack)
   New: https://discord.com/api/webhooks/<id>/<token>/slack   (Discord, Slack-shaped)
   ```

   Either way works. The script tries Discord first, Slack second,
   logs a warning if neither is set, and never crashes the streak
   bookkeeping over a missing webhook.

5. **(Optional) Per-host secrets** for `cert-expiry-monitor.yml`:

   ```
   PROD_FRONTEND_HOST   lead-scraper-frontend.onrender.com
   PROD_BACKEND_HOST    lead-scraper-backend.onrender.com
   PROD_BACKEND_URL     https://lead-scraper-backend.onrender.com
   ```

   `PROD_BACKEND_URL` doubles for the `cold-start-monitor` and the
   existing `synthetic-monitor`.

---

## How each workflow plugs in

### Composite action (the only piece every workflow shares)

`.github/actions/discord-notify/action.yml` — POSTs a Discord embed via
`curl`. Inputs:

| Input | Description |
|---|---|
| `webhook-url` | Pass via `${{ secrets.DISCORD_WEBHOOK_URL }}`. Empty → step exits 0 with a warning (no false-failure on preview-PR runs). |
| `title` | ≤ 256 chars. Markdown not rendered in the title. |
| `message` | ≤ 4000 chars. Discord markdown supported (`**bold**`, ``` ` `code` ` ```, `[link](url)`). |
| `severity` | `critical` / `error` / `warning` / `info` (default `warning`). Sets embed colour. |
| `link` | Optional URL the title links to (workflow run, tracker issue, dashboard). |

Usage pattern:

```yaml
- name: Discord alert
  if: failure()                       # or any condition
  uses: ./.github/actions/discord-notify
  with:
    webhook-url: ${{ secrets.DISCORD_WEBHOOK_URL }}
    severity: warning
    title: "thing happened"
    message: |
      Body with **markdown** and ``code``.
    link: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
```

No third-party action is pulled in — the action body is a single `curl`
in `bash`, all pinned to repo-local files.

### `synthetic-monitor.yml`

State management is handled by `.github/scripts/synthetic-monitor.mjs`,
which implements the 3-strike rule + recovery via a gist counter. The
script's alert sink is `postAlert(severity, title, description)`,
which tries `DISCORD_WEBHOOK_URL` first (Discord-native embed
matching the composite action's colour scheme) and falls back to
`SLACK_WEBHOOK_URL` only when Discord is unset.

The workflow YAML passes both secrets into the env so the operator can
pick either channel — set `DISCORD_WEBHOOK_URL` for the recommended
path; the script picks it up automatically.

### `security.yml::storage-monitor`

Job runs `python -m src.scripts.storage_report` daily. The report:

- Exits **non-zero** when usage > `STORAGE_QUOTA_BYTES * 0.90` (default
  8 GiB Pro plan disk).
- Prints a `WARN`-prefixed line when usage > 70 %.

The workflow `tee`'s stdout to a temp file, captures the exit code, and
grep-detects the `WARN` marker. The Discord step then fires with
severity `critical` for the 90 % path and `warning` for the 70 %
soft-warn.

⚠️ The grep matches `70%|WARN|WARNING` — if the wording in
`src/scripts/storage_report.py` changes, update the grep alongside.

### `mutation-test.yml::aggregate`

The aggregate job already opens / updates a single GitHub issue (label
`mutation-coverage`) on threshold breach. The new step fires Discord on
the same condition (`steps.eval.outputs.status == 'FAIL'`), pointing at
the auto-updated tracker issue.

GitHub issue is the source of truth (operator drives fix-work from
there); the Discord ping is the "open GitHub now" prompt — saves waiting
for the weekly email digest.

### `cold-start-monitor.yml` (new)

Daily 04:00 UTC probe — fetches `${PROD_BACKEND_URL}/` and measures
round-trip time. Alerts if:

- Latency > `COLD_START_THRESHOLD_SECONDS` (default 30 s), OR
- HTTP status not 2xx.

The runbook embedded in the alert message lists the likely causes in
priority order (bad deploy → Render incident → Supabase pool → Playwright
wedge). On Render `starter` plan the dyno doesn't auto-spin-down
([ADR-007](adr/007-render-not-vercel-for-backend.md)), so a >30 s
response is almost always an actionable incident, not a cold-start.

Threshold + cron are tunable:

- Repo var `COLD_START_THRESHOLD_SECONDS` — override the 30 s threshold.
- Cron line in the workflow — change cadence (e.g. hourly during business
  hours).

### `cert-expiry-monitor.yml` (new)

Weekly Mon 09:00 UTC. Uses `openssl s_client` + `openssl x509 -enddate`
to extract the `notAfter` date from the live cert on each host. Alerts
when `days_left < CERT_EXPIRY_MIN_DAYS` (default 30) OR the host is
unreachable from the runner.

Render manages TLS via Let's Encrypt auto-renewal — this monitor catches
the rare cases where auto-renewal **fails** (DNS validation drift, CNAME
change, custom-domain unverified state). The alert message points at the
Render dashboard → Custom Domains → Verify flow.

Threshold + cron are tunable:

- Repo var `CERT_EXPIRY_MIN_DAYS` — override the 30 day threshold.
- Add hosts: extend the `check_host` calls in the workflow body.

---

## Verifying end-to-end

Every workflow supports `workflow_dispatch`. After adding the secret:

```bash
# Cold start
gh workflow run cold-start-monitor.yml

# Cert expiry
gh workflow run cert-expiry-monitor.yml

# Mutation (will not actually fail unless your code regressed; the
# Discord step is gated on status == FAIL)
gh workflow run mutation-test.yml

# Storage (same — only alerts on threshold)
gh workflow run security.yml
```

To **force-test the Discord wiring** without waiting for a real alert,
manually invoke the composite action via a throwaway workflow:

```yaml
# .github/workflows/test-alert.yml (delete after verification)
name: test-alert
on: workflow_dispatch
permissions:
  contents: read
jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5  # v4.3.1
      - uses: ./.github/actions/discord-notify
        with:
          webhook-url: ${{ secrets.DISCORD_WEBHOOK_URL }}
          severity: info
          title: "🧪 Test alert"
          message: |
            This is a verification ping from `test-alert.yml`.
            If you see this in #alerts, the wiring is good.
          link: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
```

Run it; confirm the embed in `#alerts`; delete the workflow file.

---

## Suppression + dedup strategy

GitHub Actions has no native alert dedup, so the per-workflow logic
matters. Each signal already implements suppression at source:

| Signal | Dedup mechanism |
|---|---|
| Synthetic monitor | `alerted_at_streak` flag in the gist — alerts once per streak, recovery alert clears it. No re-spam on every 5-min run during an incident. |
| Storage | Workflow runs daily; one alert per day max per severity. |
| Mutation | Weekly. Single tracker issue is the operator's queue; Discord ping is informational. |
| Cold start | Daily; one alert per day max. |
| Cert expiry | Weekly. Cert lifecycle is 90-day; one alert/week for ~4 weeks before renewal succeeds. |

When you're heads-down: temporarily revoke the `DISCORD_WEBHOOK_URL`
secret value (replace with empty string). The composite action's empty-
URL guard logs an actions warning and exits 0 — alerts pile in the
workflow run log instead of pinging the channel. Restore the secret when
ready.

---

## When to ignore vs. when to act

| Alert | Ignore if | Act now if |
|---|---|---|
| `🚨 Synthetic monitor 3 fails` | Render status page shows an active incident | Otherwise. Open `gh run view <id>` for the failed-check reasons. |
| `💾 Storage > 70 %` | < 1 month-old; growing slowly | > 70 % AND `audit_results` JSONB is the top growing table — archive or upgrade |
| `💾 Storage > 90 %` | Never. Hard cap → next INSERT fails. Triage immediately. |
| `🧬 Mutation kill rate < 80 %` | The dropping target is non-security-critical | Otherwise. Open the tracker issue, add tests for survivors. |
| `🐌 Cold-start > 30 s` | Within 10 min of a deploy — wait it out | Otherwise. Open Render dashboard → service → Logs; look for OOM / restart loops. |
| `🔒 Cert expiry < 30 days` | Render Custom Domains page shows "Active" + a renewal date in the next 30 days | Otherwise. Dashboard → Verify on the affected domain row. |

---

## Cost monitoring

- Discord webhooks: **free, unlimited**.
- GitHub Actions minutes: 5 new workflows × few mins each × cron cadence
  fits inside the public-repo free tier or the 2 000 min/month
  private-repo allowance. The mutation test job is the only minute-heavy
  one (multi-hour weekly); it predated this change.

If GitHub Actions minutes ever became a concern, the lightest workflows
(cold-start, cert-expiry) could move into the existing synthetic-monitor
script as additional checks at no extra runner cost.

---

## References

- `.github/actions/discord-notify/action.yml`
- `.github/workflows/synthetic-monitor.yml` + `.github/scripts/synthetic-monitor.mjs`
- `.github/workflows/security.yml::storage-monitor`
- `.github/workflows/mutation-test.yml::aggregate`
- `.github/workflows/cold-start-monitor.yml`
- `.github/workflows/cert-expiry-monitor.yml`
- [`docs/observability.md`](observability.md) — Sentry side of the same
  observability story
- [`docs/ci-architecture.md`](ci-architecture.md) — full CI/CD inventory
- [`docs/synthetic-monitor.md`](synthetic-monitor.md) — original synthetic-
  monitor design + operator setup
- Discord webhook docs:
  - Native API: <https://discord.com/developers/docs/resources/webhook>
  - Slack-compatible endpoint: <https://discord.com/developers/docs/resources/webhook#execute-slackcompatible-webhook>
