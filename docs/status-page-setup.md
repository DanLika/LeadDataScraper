# Status Page Setup

Customer-facing health page at `status.<your-domain>`. Free, hosted on
GitHub Pages via [upptime](https://upptime.js.org). Auto-updates from
GitHub Actions cron.

> Out-of-scope for this repo — status page lives in a **separate**
> repo so it has its own deploy lifecycle and won't break when
> LeadDataScraper deploys. This doc is the setup procedure for that
> separate repo.

---

## What it shows

- ✅ / ⚠️ / ❌ status per monitored endpoint
- Last 24h / 7d / 30d / 1y uptime percentage
- Response time chart
- Incident history (auto-opened on outages, auto-closed on recovery)
- RSS feed (subscribers get pings)

Example: <https://status.upptime.js.org/>

---

## Why upptime (vs. statuspage.io, Better Uptime, Instatus)

| Tool | Cost | Hosted | GDPR-friendly | Customizable |
|---|---|---|---|---|
| **upptime** | $0 | GitHub Pages | ✅ (your data, your repo) | YAML config + custom domain |
| Atlassian Statuspage | $30+/mo | Vendor | requires DPA | Heavy customization |
| Better Uptime | $20+/mo | Vendor | requires DPA | Moderate |
| Instatus | $0 (limited) | Vendor | requires DPA | Lighter |

For single-operator + bounded users: upptime wins on cost + ownership.
Graduate to Statuspage if you ever need API integrations beyond what
GitHub Actions can provide.

---

## One-time setup

### 1. Create the separate repo

```bash
# In a directory ABOVE this repo (so it's not nested):
cd ~/git
git clone https://github.com/upptime/upptime.git bookbed-status
cd bookbed-status
rm -rf .git
git init
git add .
git commit -m "feat: initial upptime template"
gh repo create bookbed-status --public --source=. --remote=origin
git push -u origin main
```

The repo MUST be **public** — GitHub Pages is free only on public repos
for personal accounts. (Organization accounts can use Pages on private
repos at a paid tier; configure separately if you go that route.)

### 2. Configure `.upptimerc.yml`

Edit `bookbed-status/.upptimerc.yml`:

```yaml
owner: <your-github-username>
repo: bookbed-status

# What we monitor. One entry per endpoint; the status page renders
# each as a colored card.
sites:
  - name: Backend liveness
    url: https://lead-scraper-backend.onrender.com/
    expectedStatusCodes:
      - 200
    method: GET
    headers:
      User-Agent: "upptime-status-bot"

  - name: Frontend
    url: https://lead-scraper-frontend.onrender.com/login
    expectedStatusCodes:
      - 200
    method: HEAD

  - name: Supabase REST
    url: https://<your-project-ref>.supabase.co/rest/v1/
    expectedStatusCodes:
      # PostgREST returns 401 without auth — that proves it's responding.
      - 401
    method: GET

# Cron cadence — how often to probe each endpoint.
# Default 5 minutes; upptime supports 1–60 min.
status-website:
  cname: status.<your-domain>      # custom-domain CNAME
  baseUrl: /
  name: <Product> Status
  introTitle: System Status
  introMessage: |
    Real-time health of <product>. If something looks wrong, check
    here before opening a support ticket. Incidents are auto-opened
    + auto-closed.
  navbar:
    - title: Status
      href: /
    - title: GitHub
      href: https://github.com/<your-github-username>/bookbed-status
    - title: Support
      href: mailto:support@<your-domain>

# Auto-close incidents after recovery + this many minutes.
# (Avoids stale incidents lingering after a brief flap.)
notifications:
  - type: github
    enabled: true

assignees:
  - <your-github-username>
```

Commit + push.

### 3. Wire the custom domain

In your DNS provider:

```
status.<your-domain>  CNAME  <your-github-username>.github.io.
```

In the `bookbed-status` repo:

- Settings → Pages → Custom domain → `status.<your-domain>` → Save.
- Enforce HTTPS (checkbox).

GitHub provisions a Let's Encrypt cert in ~5 minutes. After it's
issued, the status page is live.

### 4. Trigger the first run

```bash
gh workflow run uptime.yml --repo <your-github-username>/bookbed-status
```

Watch the action run — it probes each site, commits `history/*.yml`
back to the repo, and re-builds the static page. ~30 seconds.

---

## Synchronizing with the synthetic monitor

The synthetic monitor (`synthetic-monitor.yml` in this repo) and the
status page are complementary:

| Signal | Purpose | Audience |
|---|---|---|
| Synthetic monitor | Operator-facing reliability — Discord ping on 3 consecutive failures. Fast detection. | Operator only. |
| Status page | Customer-facing transparency — public history, RSS, JSON API. Slow but durable. | All users. |

They share what's monitored but diverge on cadence + audience.

When the synthetic monitor pings a 3-fail outage, the status page is
already showing the affected card red — no extra wiring needed. If
you'd like a tighter coupling (e.g. the synthetic monitor *also*
updates the status page issue tracker), implement via the GitHub API
in `synthetic-monitor.mjs`.

---

## Cost

- **Storage**: GitHub repo storage (under 1 GB even after years of
  history). $0.
- **Bandwidth**: GitHub Pages free tier (100 GB/month). $0.
- **Compute**: GitHub Actions free tier (2000 min/month on free
  account, unlimited on Pro). $0–$5/mo at status-page volumes.
- **Domain**: cost of the CNAME at your DNS provider. ~$0.
- **TLS cert**: Let's Encrypt via GitHub Pages. $0.

Total: $0/month for the single-operator case.

---

## Operational notes

- **Don't commit secrets to bookbed-status/**. The status page is
  public; anything you push is publicly visible. The probes don't
  need auth (the endpoints either return 200 unauthenticated or
  return 401, both of which are acceptable shape signals).
- **History grows.** After ~1 year, `history/` will have thousands of
  YAML files. GitHub Pages handles this fine; just don't expect a
  `git clone` to be fast forever.
- **Incident auto-close**: configurable via `responseTimeColor` +
  `daysInHistogram`. Tune in `.upptimerc.yml`.

---

## Alternatives if upptime stops working

- [Better Stack](https://betterstack.com) (formerly Better Uptime) —
  paid, $20+/mo, hosted, more polished.
- [Statuspage by Atlassian](https://statuspage.io) — $30+/mo, the
  enterprise default.
- [Instatus](https://instatus.com) — $0–$15/mo, faster setup,
  vendor-hosted.

For BookBed.io specifically: revisit when paid plans exceed ~$100/mo
in customer revenue. Before that, upptime is plenty.

---

## References

- upptime project: <https://upptime.js.org>
- upptime template repo: <https://github.com/upptime/upptime>
- GitHub Pages docs: <https://docs.github.com/en/pages>
- [`docs/alerting.md`](alerting.md) — synthetic monitor + Discord
  routing (operator-facing reliability)
