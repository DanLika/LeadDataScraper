# Operator Guide — LeadDataScraper

Single-operator runbook for the lead scraping & enrichment pipeline. Read it once
end-to-end; thereafter, ⌘F for the section you need. Screenshots referenced as
`../screenshots/<NN-flow>.png` — capture procedure in section 10.

> **Audience:** the one human running this stack. The pipeline is intentionally
> single-tenant — `OPERATOR_EMAIL` enforces it at backend boot. There is no
> "team" account.

---

## At a glance

```
CSV / Google-Maps  →  leads table  →  Audit (SEO + tech)  →  Hunt (contact extraction)
                                          │                       │
                                          ▼                       ▼
                                   audit_status,          email, phone,
                                   seo_score,             business_details,
                                   audit_results          pain_points
                                                                 │
                                                                 ▼
                                                          Outreach drafts
                                                          Campaigns
                                                          Exports
```

**Where to go:**

| To do this | Section |
|---|---|
| Sign in / out | [1](#1-sign-in--sign-out) |
| Check pipeline status, active jobs | [2](#2-daily-ops--check-pipeline-status) |
| Import leads (CSV + Google Maps) | [3](#3-discover-new-leads) |
| Run audits, hunts, full pipeline, drafts | [4](#4-run-actions-on-leads) |
| Create / generate / export campaigns | [5](#5-manage-campaigns) |
| Export results | [6](#6-export--download) |
| Recover from stuck jobs, failed audits, Supabase down | [7](#7-recover-from-common-failures) |
| Cost monitoring — Gemini bill sources | [8](#8-cost-monitoring--where-gemini-bills-come-from) |
| Escalation triggers | [9](#9-when-to-escalate--call-for-help) |

**URLs:**
- Prod frontend: `https://<your-frontend>.onrender.com`
- Prod backend: `https://<your-backend>.onrender.com` *(do not hit directly; the proxy injects auth)*
- Local dev: `http://localhost:3000` (frontend), `http://localhost:8000` (backend)

---

## 1. Sign in / sign out

There is **no public signup**. Users are provisioned in the Supabase Auth
dashboard.

1. Open the app URL. If not signed in, you are redirected to `/login`.
2. Enter email + password (the same user you created in Supabase Auth).
3. On success → redirected to the dashboard.
4. To sign out: open the sidebar → click **Sign Out** at the bottom.

> **Rate limit:** 5 sign-in attempts per 60 s per IP (the counter increments
> on every attempt, pass or fail; success clears it). Trips show
> `Too many sign-in attempts. Try again in Xs.`

![Login page](../screenshots/01-login.png)

---

## 2. Daily ops — check pipeline status

Open the dashboard. The four stat cards at the top tell you the state of the
pipeline in one glance (the values come from `/stats`, cached 60 s).

| Card | What it counts |
|---|---|
| **TOTAL LEADS** | Every row in `leads` |
| **PENDING** | `audit_status = 'Pending'` (not yet audited) |
| **HIGH RISK** | `seo_score < 50` OR `high_risk_flag = true` |
| **HEALTHY** | Audited, not high-risk |

![Dashboard overview](../screenshots/02-dashboard.png)

### Active jobs (orchestration banner)

Background work — audits, hunts, discovery, enrichment, campaign generation —
runs as **orchestration jobs**. While one is in flight, a banner appears just
under the stat cards. Common labels:

- `Audit in progress…`
- `Deep Discovery Active...`
- `Deep Digital Hunt running…`
- `Enrichment running…`
- `Mining <query>...` (during Discovery)

The banner exposes a **Stop** button for whichever job is active. No banner =
nothing running.

To inspect every active job:

```bash
# from your terminal, after grabbing the session cookie from DevTools:
curl -s "https://<frontend>/api/proxy/orchestrator/active" -b "<cookie>"
```

Response: `[{id, type, status, progress, created_at, updated_at}, …]`. An empty
array means no jobs running.

For a single job:

```bash
curl -s "https://<frontend>/api/proxy/orchestrator/status/<job_id>" -b "<cookie>"
```

### Job lifecycle

| Status | Meaning |
|---|---|
| `starting` | Row inserted, async task scheduled, not yet processing |
| `running` | Actively processing chunks (chunk size = 50 leads) |
| `completed` | All chunks done |
| `failed` | Aborted with error |
| `stopped` | You stopped it via `/orchestrator/stop/{job_id}` |

`progress` is `{processed, total}` and updates per chunk.

If the backend restarts mid-job, `recover_interrupted_jobs()` runs at startup
and flips any `running` rows from the previous boot. You can safely re-run the
same action — chunk processing is idempotent (upserts on `unique_key`).

### Settings modal (read-only config overview)

Sidebar → **Settings** opens **System Settings** with an "API Configuration"
section. It is **read-only** — env vars live in `.env` (backend) and
`.env.local` (frontend), not in the UI. To change anything in there, edit the
file, restart the affected service, and reload the modal.

![Active job banner](../screenshots/03-job-banner.png)

---

## 3. Discover new leads

Two import paths. Pick whichever you have data for.

### 3a. CSV upload (drag-drop or button)

Two ways to import a `.csv`:

1. **Drag-and-drop** the file anywhere on the dashboard.
2. Click the **Upload CSV** button.

What happens:

- File size: **50 MB** hard cap (backend). Dashboard rejects > 10 MB client-side
  with `File is too large. Maximum size is 10MB.`
- Content-type must be `text/csv` (or `application/vnd.ms-excel`). Wrong type →
  `Only CSV files are accepted (got .xyz).`
- Columns are renamed to the canonical schema by **Gemini** (`GeminiMapper`).
  This is **one Gemini call per upload** — the only AI call in the import path.
- Each row upserts into `leads` keyed on `unique_key`. New leads start at
  `audit_status = 'Pending'`.

Common toasts:

| Toast | Means |
|---|---|
| Success toast (server-supplied summary) | Done; row count is in the message |
| `Upload failed (HTTP 413)` | File > 50 MB |
| `Upload failed (HTTP 422)` | Couldn't parse CSV (encoding, header row, etc.) |
| `Upload failed — backend unreachable.` | Network / backend down — retry |
| `Upload already in progress — wait for it to finish.` | One upload at a time |
| `Only CSV files are accepted (got .xyz).` | Wrong extension |
| `File is too large. Maximum size is 10MB.` | Client-side cap; backend allows up to 50 MB but the dashboard refuses earlier to spare a wasted upload |

> **Rate limit:** 5 uploads/minute.

![CSV upload flow](../screenshots/04-csv-upload.png)

### 3b. Google-Maps discovery (Deep Discovery)

Sidebar → **Deep Discovery** opens the modal.

| Field | Example |
|---|---|
| **Query** | `dentist`, `roofing contractor`, `auto repair shop` |
| **Location** | `Mostar, BiH`, `Brooklyn, NY`, `Berlin, Germany` |

Click **Start Discovery**.

What happens:

- A `discovery` orchestration job is created (POST `/discovery/start`).
- Playwright opens Google Maps, scrolls the side-panel, and harvests results.
- Each result is upserted as a lead with `lead_source = 'google_maps'`,
  `audit_status = 'Pending'`, and a stable `unique_key` derived from the
  Google Maps place URL.
- Banner shows `Mining {query}...` while running. Typically 30–60 s per
  ~20 results.

When the banner clears, refresh the page to see the new rows.

> **Cost: zero Gemini calls.** Discovery is pure Playwright + regex.
> Bandwidth + Google rate-limits are the only concerns.
>
> **Rate limit:** 5 discovery jobs/minute (run one at a time in practice).

![Deep Discovery modal](../screenshots/05-deep-discovery.png)

---

## 4. Run actions on leads

Every bulk action shows a `confirm()` dialog naming the lead count + a cost
warning. **Read those dialogs** — they're not boilerplate.

> **Button labels may differ from this doc.** The confirm-dialog text is the
> canonical signal — if you see one of the dialog phrases quoted below, you
> are on the action this section describes, regardless of the button label.

### 4a. SEO audit (single + bulk)

**Per-lead audit:** open a lead row → **Re-audit** in the detail panel.
Toast: `Re-audit queued.`

**Bulk audit:** Filter to **Pending** (FilterBar) → click the bulk **Audit** /
**Process All** button. Dialog:

> `Run SEO audit on N leads? This may take several minutes and hit Google rate limits.`

What happens per lead (in `ParallelAuditor`):

1. HTTP GET the `website` field (aiohttp, SSRF-guarded).
2. Parse HTML for tech-stack flags (regex).
3. Compute `seo_score` in pure Python.
4. Persist `audit_status`, `audit_results`, `seo_score`.

**Failure modes** write specific values to `audit_status`:

| Value | Cause |
|---|---|
| `Failed` | Generic; check `last_error` |
| `Timeout` | Site didn't respond inside the deadline |
| `403 Forbidden` | Site bans scrapers |
| `404 Not Found` | URL is stale |
| `Invalid URL` | `website` field malformed |

`last_error` (up to 500 chars of the underlying exception) is visible in the
lead detail panel.

> **Cost: zero Gemini calls** (pure aiohttp + regex).
>
> **Rate limit:** 3/min `/process-all`, 20/min `/process-lead`.

![Audit running](../screenshots/06-audit-running.png)

### 4b. Deep Hunt (contact extraction + AI summarisation)

Single or bulk **Deep Hunt** button. Bulk dialog:

> `Launch Deep Digital Hunt on N leads? Playwright will scrape each website (slow + bandwidth-heavy).`

Per lead it:

1. Opens the website in a headless Chromium context (shared browser pool).
2. Extracts contact `email`, `phone`, social links, key offerings.
3. Calls **Gemini** to summarise `business_details`, `contact_details`,
   `pain_points`, and `email_hook`.

> **Cost: 3–4 Gemini calls per lead** (`business_details`, `contact_details`,
> `pain_points`, plus the enrichment-engine summarisation if the hunt path
> invokes it for a lead). One of the biggest contributors to the monthly bill.
>
> **Rate limit:** 3/min `/hunt-all`, 20/min `/hunt-lead`.

![Deep Hunt running](../screenshots/07-deep-hunt.png)

### 4c. Full pipeline (audit + enrich + hunt)

Dashboard → bulk action menu → **Run Full Pipeline**. Dialog:

> `Run FULL pipeline (audit + enrich + hunt) on N leads? This is the most expensive operation — multi-minute, multi-source scrape.`

Chains audit → enrich → hunt in a single orchestration job. Per lead can incur
**3–5 Gemini calls**. Use only on a small filtered set unless you have budget
explicitly cleared.

### 4d. Outreach draft (single, on demand)

Open a lead row → **Draft Outreach Email**.

- Backend calls `/draft-outreach` (1 Gemini call). Response:
  `{subject, draft, lead_name, lead_email, operator_name}`.
- Modal opens with **Subject** + **Body**.
- Buttons: **Copy** → toast `Draft copied to clipboard!`;
  **Copy Subject** → `Subject copied!`;
  **Open in Gmail** (deep link, prefills both).
- Sign-off is `OPERATOR_NAME` from backend `.env`. Default `"Your Name"` —
  set this once before sending real outreach.

Same pattern for **LinkedIn Draft** (`/draft-linkedin`). Toast:
`LinkedIn message copied — paste into the Connect dialog.`

> **Cost: 1 Gemini call per draft.** Cheap.
>
> **Rate limit:** 20/min each.

![Outreach draft modal](../screenshots/08-outreach-modal.png)

### 4e. AI chat — natural-language actions

Floating chat in the bottom-right corner. Placeholder:
`Ask AI to audit, find emails, or filter leads...`

Examples it understands:

| You type | What it does |
|---|---|
| `How many leads are in the database?` | Auto-runs `STATUS_CHECK`, answers inline |
| `Show me all high-risk dentists` | Auto-runs `DATABASE_QUERY`, surfaces matches |
| `What patterns do you see?` | Auto-runs `GET_INSIGHTS` (Gemini) |
| `Find 5 plumbers in Sarajevo` | Returns a **plan card** → click **Confirm & Execute** |
| `Audit the 10 newest leads` | Plan card |
| `Draft outreach for ACME Corp` | Plan card |

Plan card shows `task`, `params`, and Gemini's reasoning. Click
**Confirm & Execute** to run; close the card to dismiss.

Read-only tasks (`DATABASE_QUERY`, `STATUS_CHECK`, `GET_INSIGHTS`) **auto-execute**
without a confirm step.

> **Cost: 1 Gemini call per chat submit** (routing), plus downstream calls if
> you confirm a writing task.
>
> **Rate limit:** 10/min `/ask`, 10/min `/execute`.

---

## 5. Manage campaigns

Sidebar → **Campaigns** (route: `/campaigns`).

### 5a. Create a campaign

Empty state shows **"No Campaigns Yet"** + a **Create Campaign** button.

Form fields:

| Field | Values |
|---|---|
| **Name** | Free text (required) |
| **Channel** | `email` / `linkedin` / `multi` |
| **Segment filter** | Optional. Restricts the campaign's lead pool to one segment (e.g. `dentists`) |

Click **Create Campaign**. New row appears with status `draft`.

![Create campaign](../screenshots/09-create-campaign.png)

### 5b. Generate messages

Open a campaign → **Generate**.

- Backend calls `/campaigns/{id}/generate`.
- **Per lead in the campaign's pool**, Gemini drafts a message for the
  campaign's channel.
- Toast: `Generated outreach for N lead(s) ✓`.

> **Cost: 1 Gemini call per lead per generate.** A 100-lead campaign = 100
> calls — easily the most expensive single operator action. Segment-filter the
> campaign first.
>
> **Rate limit:** 3 generate calls/minute.

![Generated messages](../screenshots/10-campaign-messages.png)

### 5c. Preview, edit, export

- Click a message row → **Message Preview** modal opens with the full draft.
- **Export** the campaign → CSV downloads (one row per message + lead context).
- Toast: `Export downloaded.`

Campaign statuses: `draft`, `active`, `paused`, `completed`. SMTP / LinkedIn
sending is **not** wired up — `sent`/`delivered`/`replied`/`bounced` are
forward-compat fields on `campaign_messages`. Today, "send" means copy/paste
out of the preview modal or out of an export.

> **Rate limit:** 12/hour `/campaigns/{id}/export`.

---

## 6. Export & download

Three export endpoints — prefer the streaming ones unless you need a file on
the backend disk for another tool.

| Action | Endpoint | What you get |
|---|---|---|
| Full export | `GET /export/download` | Every lead, every column |
| Outreach-ready | `GET /export/outreach` | Only leads with `email` + `outreach_score ≥ threshold` |
| Legacy disk-write | `GET /export` | Writes CSV to backend disk, returns path |

Streaming exports paginate 200 rows at a time via the keyset cursor — memory
bounded, safe on a multi-thousand-row leads table. Trigger from the dashboard's
**Export** menu. Success toast: `Export generated!`.

> **CSV-injection guard:** every string cell starting with `= @ + -` is
> prefixed with `'` before write. Excel/Sheets/Numbers render it as literal
> text instead of executing `=HYPERLINK(...)` etc. on open.
>
> **Rate limit:** 6/hour each.

---

## 7. Recover from common failures

### 7a. Stuck / zombie job (`running` for hours)

A job sits at `running` and `updated_at` hasn't moved in hours.

**Auto-heal (daily CI sweep):** `check_orphans_and_zombies.py` runs nightly
and flips any `running` job older than **`ZOMBIE_THRESHOLD_HOURS = 4`** to
`failed`. You'll see the result in the next morning's `security.yml` workflow
run.

**Manual stop:**

```bash
curl -X POST "https://<frontend>/api/proxy/orchestrator/stop/<job_id>" \
  -b "<cookie>"
```

Or click the **Stop** button on the job banner if it's still visible.

**Hard recovery:** if the backend restarts (e.g. Render redeploy),
`recover_interrupted_jobs()` runs at startup and resets every `running` row
from the previous boot.

### 7b. Stuck leads (Pending / Processing > 24 h)

`STUCK_THRESHOLD_HOURS = 24`. The same sweep reports these but does **not**
auto-heal them — they might be a slow domain that just needs a retry. Manual
options:

- Filter to **Pending** → click the bulk **Audit** button (idempotent re-run).
- Or edit the row in Supabase Studio: set `audit_status = 'Failed'` and move
  on.

### 7c. Failed audit (Timeout / 403 / 404 / Invalid URL)

The lead's `audit_status` is one of the failure values; `last_error` has the
full underlying exception. Decision table:

| `audit_status` | What to do |
|---|---|
| `Timeout` | Site is slow. Retry off-peak. |
| `403 Forbidden` | Site bans scrapers. Drop the lead or switch to manual. |
| `404 Not Found` | Stale URL. Edit `website` in Studio or drop the row. |
| `Invalid URL` | Malformed URL field. Edit the row. |
| `Failed` (other) | Read `last_error`. Common causes: SSL handshake, DNS failure, connection refused. |

### 7d. Supabase down (503 from backend)

If `db.client` is `None` (Supabase unreachable at boot), endpoints that touch
the DB return **503**. The dashboard surfaces this as
`<Action> failed — backend unreachable.`

1. Check Supabase status: <https://status.supabase.com>.
2. Check the specific project: Supabase dashboard → Project → Logs.
3. If the project is paused (free-tier auto-pause), un-pause it.
4. Restart the backend (Render → **Manual Deploy**).
5. Once Supabase is back, the next request rehydrates `db.client` lazily —
   no second restart needed.

### 7e. Backend restart mid-job

Render redeploys, or `uvicorn` crashes. On startup,
`recover_interrupted_jobs()` resets any `running` jobs from the previous boot.
Leads being processed at the time stay in whatever state they were in — the
upserts are idempotent, so re-running the same action picks up cleanly.

### 7f. Browser offline

The **OfflineBanner** appears at the top whenever `navigator.onLine` flips
`false`. State-changing fetches are queued in IndexedDB (`offlineQueue`); on
reconnect the queue auto-drains and the banner clears. The banner shows the
queue count while you wait.

### 7g. "Clear All Leads" returns 403

You clicked **Clear All Leads** and got a 403. Two causes:

1. `ADMIN_TOKEN` is unset (or doesn't match between backend `.env` and frontend
   `.env.local` / Render env vars). The proxy can't inject the header.
2. You don't have a valid Supabase Auth session. Sign back in.

> **Rate limit on `DELETE /leads/clear`:** 3/hour. Hitting it too many times in
> a panic = lockout.

---

## 8. Cost monitoring — where Gemini bills come from

**Every Gemini call site**, sorted by per-operator impact:

| Operation | Frequency | Gemini calls |
|---|---|---|
| **Bulk Deep Hunt / Full Pipeline** | per lead | 3–4 (business_details + contact_details + pain points, plus enrichment summary on the hunt path) |
| **Campaign generate** | per lead per generate | 1 |
| **CSV upload (column mapping)** | per upload | 1 |
| **Outreach draft** (single) | per click | 1 |
| **LinkedIn draft** (single) | per click | 1 |
| **AI chat `/ask`** | per submit | 1 (routing) + downstream if confirmed |
| **`/insights` AI Strategic Analysis** | per visit | 1 |

**Operations with ZERO Gemini cost** (pinned by tests — do not "optimise" them
by adding AI):

- **Google-Maps Discovery** (Playwright + DOM parsing)
- **SEO audit** (`aiohttp` + regex tech detection)
- `segment_lead` (pure regex over a keyword list)
- `calculate_outreach_score` (pure Python; pinned in
  `tests/test_outreach_score_properties.py` — does NOT read `seo_score`)

### Where to see the bill

- **Google AI Studio** → API key → **Usage** tab. Daily breakdown.
- The `test_ai_cost_budget.py` test (live tier, run with
  `GEMINI_API_KEY=… pytest tests/test_ai_cost_budget.py`) prints a per-task
  cost breakdown — use it as a back-of-envelope before kicking off a large
  campaign generate or full pipeline run. Current budget: ≤ $0.50 per 20-lead
  100-call pipeline.

### Cost-controlling reflexes

1. **Filter first, then act.** Running **Process All** on 1 000 unfiltered
   leads is a budget event.
2. **Read the confirm dialogs** — they show the lead count for a reason.
3. **Segment-filter campaigns** before pressing **Generate**. Generating
   messages for a 10 000-lead campaign is the single most expensive thing the
   UI lets you do.
4. **Don't multi-tab actions.** The per-button `disabled + aria-busy` guard
   prevents double-fire, but two tabs bypass it.
5. **Use AI chat for read-only questions.** `DATABASE_QUERY` and
   `STATUS_CHECK` are cheap (one routing call + one query) versus clicking
   around several views.

---

## 9. When to escalate / call for help

Open a GitHub issue or DM the maintainer when:

- A job is **stuck in `running`** for over an hour AND the dashboard banner
  shows no progress AND `/orchestrator/active` doesn't list it → a torn DB
  row, not auto-recoverable.
- The **dashboard loads but every action 403/503s.** Likely env-var drift —
  `API_SECRET_KEY` mismatch between proxy and backend, or `ALLOWED_ORIGINS`
  on Render frontend service is unset / wrong.
- **Supabase Studio RLS alert** on `leads`, `campaigns`, `campaign_messages`,
  or `orchestration_jobs` → RLS was supposed to be deny-all on all four.
  Treat as a security incident.
- **Gemini costs spike 3× baseline** without an obvious cause. Cross-check
  the operations table above first — usually a forgotten bulk Hunt or a
  campaign generate on an unfiltered set.
- **Render deploy fails with `HashMismatch`** during `pip install`. Lockfile
  desynced — run `make lock-python` locally and commit.
- **`workflow-drift` GitHub issue auto-opens.** Means a CI workflow file
  changed without committing the hash snapshot — verify the change is
  intentional, then `make workflow-hashes`.
- **`flaky` GitHub issue auto-opens AND the failing test is in
  `ci.yml::flaky-gate`'s window.** New PRs touching the same file will be
  blocked until the test is fixed or quarantined.

When you escalate, have:

- Affected `job_id` (or "no job started" if pre-flight)
- Time of the last known-good action
- Last 50 lines of Render logs (Backend service → Logs)
- Browser console errors (F12 → Console)

---

## 10. Appendix — capture screenshots

Every `![*](../screenshots/*.png)` link above expects an image at that path.
The doc ships with empty placeholders so the markdown structure is reviewable
without the binaries. To fill them in:

1. Boot the dev stack:

   ```bash
   # terminal 1 — backend
   uvicorn backend.main:app --reload --port 8000

   # terminal 2 — frontend
   cd frontend && npm run dev
   ```

2. Sign in at `http://localhost:3000/login` with a **dedicated test Supabase
   Auth user** — never production credentials. Provision one in the Supabase
   Auth dashboard if you don't have one yet.

3. Walk each flow below and capture via:
   - macOS: `Cmd+Shift+4` (region) or `Cmd+Shift+5` (window).
   - Browser: DevTools → Command menu (`Cmd+Shift+P`) → "Capture full size
     screenshot".

4. Save each into `docs/screenshots/` with the exact filename from the
   checklist.

5. Commit the doc + screenshots together: `git add docs/screenshots/*.png
   docs/runbooks/operator-guide.md`.

### Screenshot checklist

- [ ] `01-login.png` — login form, blank
- [ ] `02-dashboard.png` — fully loaded dashboard, stat cards + lead table
      visible (use a non-empty dev DB)
- [ ] `03-job-banner.png` — orchestrator banner during a job (start a
      Discovery to make one)
- [ ] `04-csv-upload.png` — drag-drop hover state OR upload-in-progress toast
- [ ] `05-deep-discovery.png` — Deep Discovery modal open, both fields filled
- [ ] `06-audit-running.png` — audit banner + a few rows mid-audit
- [ ] `07-deep-hunt.png` — Deep Hunt banner during a run
- [ ] `08-outreach-modal.png` — outreach draft modal with subject + body
- [ ] `09-create-campaign.png` — empty state + Create Campaign modal
- [ ] `10-campaign-messages.png` — campaign view with generated messages

> **Privacy:** screenshots may capture real lead names / emails. If using prod
> data, blur the email and personal-name columns before committing. The dev
> stack against a throwaway DB avoids the issue entirely.

---

## 11. Appendix — backend API reference

Every endpoint except `GET /` requires the `X-API-Key` header. The Next.js
proxy injects it server-side from the `API_SECRET_KEY` env var — you never
set it from the browser.

| Method | Path | Rate | Purpose |
|---|---|---|---|
| GET | `/` | — | Liveness probe (`{"status":"ok"}`) |
| POST | `/metrics` | 60/min | Web-vitals RUM ingest |
| GET | `/leads` | 30/min | Cursor-paginated lead list |
| POST | `/upload` | 5/min | CSV upload, 50 MB cap, 1 Gemini call (mapping) |
| POST | `/process-lead` | 20/min | Single-lead audit (no Gemini) |
| POST | `/process-all` | 3/min | Bulk audit job |
| GET | `/audit-status` | 60/min | Audit progress |
| POST | `/audit/stop` | 10/min | Stop audit |
| GET | `/health/schema` | 12/min | Schema sanity probe |
| POST | `/ask` | 10/min | AI chat routing — 1 Gemini |
| GET | `/insights` | 10/min | Strategic insights — 1 Gemini |
| GET | `/stats` | 30/min | Cached 60 s aggregates |
| POST | `/draft-outreach` | 20/min | Outreach email — 1 Gemini |
| POST | `/draft-linkedin` | 20/min | LinkedIn DM — 1 Gemini |
| POST | `/execute` | 10/min | Execute a confirmed plan from `/ask` |
| POST | `/hunt-lead` | 20/min | Single-lead Deep Hunt — ~3 Gemini |
| POST | `/hunt-all` | 3/min | Bulk Deep Hunt |
| POST | `/discovery/start` | 5/min | Google-Maps Discovery (zero Gemini) |
| POST | `/enrich/start` | 10/min | Enrichment job |
| DELETE | `/leads/clear` | 3/hour | **DESTRUCTIVE.** Needs `X-Admin-Token` |
| POST | `/orchestrator/start` | 3/min | Start orchestrator job |
| GET | `/orchestrator/status/{job_id}` | 60/min | Single job status |
| GET | `/orchestrator/active` | 60/min | List active jobs |
| POST | `/orchestrator/stop/{job_id}` | 10/min | Stop a running job |
| GET | `/export` | 6/hour | Legacy disk-write export |
| GET | `/export/download` | 6/hour | Streaming full CSV |
| GET | `/export/outreach` | 6/hour | Streaming outreach-ready CSV |
| POST | `/campaigns` | 20/min | Create campaign |
| GET | `/campaigns` | 60/min | List campaigns |
| GET | `/campaigns/{id}` | 60/min | Campaign + messages |
| POST | `/campaigns/{id}/generate` | 3/min | Generate messages (Gemini per lead) |
| POST | `/campaigns/{id}/start` | 10/min | Mark campaign active |
| POST | `/campaigns/{id}/export` | 12/hour | Export campaign CSV |

---

## 12. Appendix — required env vars

### Backend `.env`

| Var | Required | Purpose |
|---|---|---|
| `API_SECRET_KEY` | yes | The `X-API-Key` every authed endpoint checks |
| `ADMIN_TOKEN` | yes | Required for `DELETE /leads/clear` |
| `SUPABASE_URL` | yes | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | yes | Server-side DB writes |
| `GEMINI_API_KEY` | yes | All AI calls |
| `ALLOWED_ORIGINS` | yes | CORS + Origin gate |
| `OPERATOR_NAME` | optional | Signs outreach drafts (otherwise `"Your Name"`) |
| `OPERATOR_EMAIL` | optional | Enforces single-tenant assertion at boot |

### Frontend `.env.local`

| Var | Required | Purpose |
|---|---|---|
| `BACKEND_URL` | yes | Where the proxy forwards |
| `API_SECRET_KEY` | yes | Server-side; proxy injects |
| `ADMIN_TOKEN` | yes | Server-side; proxy injects on destructive paths |
| `ALLOWED_ORIGINS` | yes | Origin gate for state-changing fetches |
| `NEXT_PUBLIC_SUPABASE_URL` | yes | Auth client URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | yes | Auth client key (RLS-restricted anon) |

> **Render parity:** `ADMIN_TOKEN` and `ALLOWED_ORIGINS` **must** be set on
> the **frontend** Render service too — not just the backend. Without them
> the **Clear All Leads** button 403s and every prod state-change fail-closed
> 403s.

---

## 13. Appendix — quick reference cheatsheet

```
Sign in              /login                                 (Supabase Auth, 5/min throttle)
Sign out             Sidebar → Sign Out                     (POST /api/auth/signout)

See active jobs      curl /api/proxy/orchestrator/active
Stop a job           curl -X POST /api/proxy/orchestrator/stop/{job_id}

Import (CSV)         drag-drop OR Upload CSV button         (50 MB cap, 5/min, 1 Gemini)
Import (Maps)        Sidebar → Deep Discovery               (zero Gemini, 5/min)

Audit one            Lead row → Re-audit                    (zero Gemini)
Audit all            Process All button                     (zero Gemini, 3/min)
Hunt one             Lead row → Deep Hunt                   (~3 Gemini)
Hunt all             Bulk Deep Hunt button                  (~3 Gemini/lead, 3/min)
Full pipeline        Run Full Pipeline button               (3-5 Gemini/lead)

Draft email          Lead row → Draft Outreach Email        (1 Gemini, 20/min)
Draft LinkedIn       Lead row → Draft LinkedIn              (1 Gemini, 20/min)
AI chat              Floating chat, bottom-right            (1 Gemini/submit, 10/min)

New campaign         /campaigns → Create Campaign
Generate messages    Campaign → Generate                    (1 Gemini/lead, 3/min)
Export campaign      Campaign → Export                      (12/hour)

Full export          Dashboard → Export → Download          (6/hour)
Outreach-ready       Dashboard → Export → Outreach          (6/hour)

Clear all leads      Dashboard → Clear All Leads            (DESTRUCTIVE, 3/hour, X-Admin-Token)
```
