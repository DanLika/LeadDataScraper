# Incident Runbook

Five scenarios that take LeadDataScraper down or threaten its data. **Read
this BEFORE the incident.** During one, jump straight to the section that
matches the symptom and follow the steps top-to-bottom. Each scenario has
the same shape:

1. **Detection** — what the alert / symptom looks like
2. **Triage** — quick branch decisions before touching anything
3. **Mitigation** — copy-paste-ready recovery steps
4. **Post-mortem template** — what to fill in afterward

Companion runbooks:
- [`docs/runbooks/operator-guide.md`](operator-guide.md) — day-to-day ops
  (not incident-time)
- [`docs/observability.md`](../observability.md) — Sentry wiring + log
  schema
- [`docs/alerting.md`](../alerting.md) — Discord routing for non-Sentry
  signals
- [`docs/secret-inventory.md`](../secret-inventory.md) — secrets + rotation
  cadence ([Faza 8.10](#5-security-incident-sev-1))
- [`docs/ci-architecture.md`](../ci-architecture.md) — CI gate inventory
- `backup-verify-deep.yml` — PITR verification workflow
  ([Faza 5.15](#4-data-corruption-sev-1))

---

## At a glance

| # | Scenario | Default SEV | First detection | Realistic time-to-mitigation |
|---|---|---|---|---|
| [1](#1-backend-down-sev-1) | Backend down | **SEV-1** | Synthetic monitor 3-fail Discord ping | **5–15 min** (Render rollback) |
| [2](#2-supabase-down-sev-1) | Supabase down | **SEV-1** | 503 on DB-touching endpoints, "backend unreachable" toasts | **wait** (managed; no rollback option) |
| [3](#3-gemini-api-outage-sev-2) | Gemini API outage | **SEV-2** | AI features fail (drafts, /ask, hunt, insights) | **minutes to hours** (depends on Google) |
| [4](#4-data-corruption-sev-1) | Data corruption | **SEV-1** | schema-drift / referential-integrity / JSONB-shape / null-audit CI gates red OR manual Studio spot | **30 min – 2 h** (PITR restore + reconciliation) |
| [5](#5-security-incident-sev-1) | Security incident | **SEV-1** | gitleaks CI flag, anomalous Sentry / Supabase access, container scan CVE, weird Discord webhook traffic | **15 min** (rotate) + **1–2 h** audit |

---

## Severity tiers

| Tier | Meaning | Operator response |
|---|---|---|
| **SEV-1** | Full outage, data at risk, or active exploitation | Drop everything. Open this doc + tools. Mitigate first, root-cause after. |
| **SEV-2** | One feature broken, no data loss, no active threat | Mitigate within an hour. |
| **SEV-3** | Cosmetic, non-critical regression, or false alarm | Note in incident log, fix in next workday batch. |

For a single-operator setup these are mostly self-discipline. They become
contractual once paying users land. Until then: SEV-1 = real now, SEV-2 =
real today, SEV-3 = real this week.

---

## Tools to open during an incident

Bookmark these as a folder; muscle-memory beats search during stress.

- **Render dashboard** → both `lead-scraper-backend` + `lead-scraper-frontend`
  services (Status / Deploys / Logs / Environment)
- **Render status page** — <https://status.render.com>
- **Supabase project dashboard** → Logs / Auth / Database / Advisors
- **Supabase status page** — <https://status.supabase.com>
- **Google AI Studio** → API key dashboard / usage
- **Google Cloud Status** — <https://status.cloud.google.com> (Gemini lives under "Generative AI on Vertex AI" / GAI)
- **Sentry** — both projects (backend + frontend)
- **Discord `#alerts`** — alert history
- **GitHub Actions** — last green runs, ability to `gh workflow run` recovery jobs
- **This doc** — `docs/runbooks/incidents.md`
- **The incident log** — `docs/runbooks/incidents/YYYY-MM-DD-<slug>.md`
  (create on incident open, fill during, complete at post-mortem)

---

## 1. Backend down (SEV-1)

### 1.1 Detection

You become aware via one or more of:

- 🚨 Discord ping: `Synthetic monitor: 3 consecutive failures` (see
  [`docs/alerting.md`](../alerting.md))
- 🐌 Discord ping: `Cold-start / latency probe failed`
- Sentry: spike of `ConnectionError`, `Timeout`, or sudden traffic-to-zero
- Dashboard UI: every action toasts `<Action> failed — backend unreachable.`
- Manual probe:
  ```bash
  curl -fsS -m 10 "$BACKEND_URL/" || echo "DOWN"
  ```

### 1.2 Triage

Decide in this order — first match wins:

1. **Is Render itself down?** → status.render.com banner red. **Do
   nothing yet** — wait for their fix; document the timeline in the
   incident log. Skip to post-mortem when their status page clears.

2. **Did a deploy land in the last 60 minutes?** Render dashboard →
   backend service → Deploys.
   - Last deploy status **failed** or **building** for > 5 min →
     **bad deploy.** Section 1.3a.
   - Last deploy succeeded but the service is unhealthy →
     **healthy-deploy-bad-code.** Section 1.3a (rollback) or 1.3b
     (fix-forward) depending on confidence.

3. **No recent deploy, service unhealthy?** Likely OOM, restart loop,
   wedged Playwright pool, or Supabase pool saturation. Section 1.3c.

4. **Service status "Running" but probe still 503/timeout?** Usually a
   wedged uvicorn worker — `_assert_single_tenant_if_enforced` lifespan
   tripped, `db.check_schema()` blocking, or an
   `recover_interrupted_jobs()` hang. Section 1.3d.

### 1.3 Mitigation

#### 1.3a Rollback (recent bad deploy)

Render: **Dashboard → backend service → Deploys → previous green deploy → Roll Back.**
This restores the prior image immediately. Render switches DNS routing in
~30s.

For tag-driven deploys: `git revert <bad-commit> && git push` re-fires the
GHCR + cosign chain (`deploy-backend.yml`) producing a fresh image. Slower
than dashboard rollback, but captured in git history (preferable for
post-mortem traceability).

#### 1.3b Fix-forward (you know the bug, fix < 10 min)

If you can trivially identify the bug (and you've already mitigated user-
visible damage some other way — Render maintenance mode, or simply
acknowledging the brief outage), open a PR with the fix, get it through
CI, merge. The deploy chain handles the rest.

> **Don't fix-forward under pressure.** Roll back first, fix in a
> calmer follow-up. The Render rollback is 30 seconds; a panicked
> hotfix is a second outage.

#### 1.3c Service unhealthy, no recent deploy

```bash
# Render logs — top of stack
# Dashboard → backend service → Logs

# Look for (in order of likelihood):
# - "Killed" / OOM kernel messages          → memory pressure, restart pod
# - "FATAL: too many connections"           → Supabase pool saturation
# - "Playwright" + "Target closed"          → Chromium pool wedged
# - "RuntimeError: Single-tenancy check"    → OPERATOR_EMAIL invariant tripped (someone created a 2nd Supabase Auth user)
# - "ConnectionRefused" to Supabase URL     → Supabase outage (jump to §2)
```

Mitigation by cause:

| Log signal | Action |
|---|---|
| OOM / memory pressure | Render → service → Manual Deploy → "Clear build cache & deploy" forces a restart. If recurring, escalate to plan upgrade. |
| `too many connections` | Supabase pooler saturation. Restart the backend; if recurring, the orchestrator's chunk size needs reducing (`task_orchestrator.py::chunk_size`). |
| Playwright wedge | Backend restart forces `aclose()` on the browser pool. |
| Single-tenancy check tripped | Supabase Auth → Users → delete the extra user. Then redeploy. |

#### 1.3d Wedged uvicorn worker

Render dashboard → service → **Shell** (or **Manual Deploy** if Shell
unavailable on the plan). Look at the boot logs in real time:

```
Lead Data Scraper Backend Starting...
Single-tenancy assertion passed (operator=...).
[hung here for >30s]
```

The lifespan blocks on `db.check_schema()` + `recover_interrupted_jobs()`.
If Supabase is slow, both stall. If you see the boot wedged here for
> 60 s:

1. **Verify Supabase is healthy** (status page + project dashboard).
   If Supabase is down → jump to §2.
2. **If Supabase is fine but the wedge persists**, manually mark
   `orchestration_jobs` rows from the last boot as `failed` in Supabase
   Studio:

   ```sql
   UPDATE orchestration_jobs
      SET status='failed', updated_at=now()
    WHERE status='running' AND updated_at < now() - interval '1 hour';
   ```

3. Restart the backend (Render → Manual Deploy). Lifespan's
   `recover_interrupted_jobs()` now has nothing to process and clears
   immediately.

> **Future improvement** (not yet implemented): a `LIFESPAN_SKIP_RECOVER=1`
> env flag would let the operator skip the recover step entirely
> without touching SQL. Tracked in the §1.5 action items when
> a wedge incident exercises this path.

### 1.4 Verification

After mitigation, confirm:

1. `curl -fsS "$BACKEND_URL/" | jq` returns `{"status":"ok"}`.
2. Synthetic monitor's next run (within 5 min) lands green; Discord posts
   `✅ Synthetic monitor: recovered`.
3. Sentry rate returns to baseline (5–10 min after recovery).
4. Pick a real workflow (e.g. `/ask` with a STATUS_CHECK question)
   end-to-end via the dashboard.

### 1.5 Post-mortem template (Backend down)

Save as `docs/runbooks/incidents/YYYY-MM-DD-backend-down.md`:

```markdown
# YYYY-MM-DD — Backend down

**Severity:** SEV-1
**Duration:** HH:MM UTC → HH:MM UTC (XXm)
**Detected by:** synthetic monitor / Sentry / operator manual / customer report
**Tracking:** <link to GitHub issue or this file>

## TL;DR
One-sentence summary. "Bad deploy at 14:32 UTC blocked uvicorn boot due
to a missing import in `process-all` handler; rolled back at 14:45."

## Timeline (UTC)
- HH:MM — first detection signal (Discord ping / Sentry event)
- HH:MM — operator opens incident
- HH:MM — triage decision: rollback / fix-forward / wait
- HH:MM — mitigation action taken
- HH:MM — synthetic monitor green
- HH:MM — operator declares recovery

## Root cause
The actual technical cause. Cite file:line.

## What helped
- Discord 3-fail ping (vs polling Sentry manually) — caught it within 15 min vs ~hours otherwise
- Render rollback button — single click, recovered in 30 s
- ...

## What didn't help
- Searching for the cause IN Sentry while the service was down — Sentry events from a wedged process can be delayed up to 5 min
- Trying to fix-forward on `main` instead of rolling back first
- ...

## Action items
- [ ] Add a CI test that catches the regression class. Owner: <self>. Due: <date>.
- [ ] Update §1.3 of incidents.md if a new failure mode was observed.
- [ ] (If applicable) bump synthetic monitor's `ALERT_THRESHOLD` if early detection was missed.
```

---

## 2. Supabase down (SEV-1)

### 2.1 Detection

- Backend logs / Sentry: `httpx.ConnectError` to the Supabase URL, or
  `postgrest.APIError` with no specific PGRST code.
- Backend returns **503** on every DB-touching endpoint (the `db.client`
  check fails fast).
- Dashboard UI: every action toasts `<Action> failed — backend unreachable.`
  (note: same toast as backend-down — distinguish by checking the backend
  liveness probe — see Triage).
- Synthetic monitor: `/health/schema` fails before `/` does (DB-dependent
  check fails first while the liveness probe still responds).

### 2.2 Triage

1. **Backend liveness probe healthy?**
   ```bash
   curl -fsS "$BACKEND_URL/" | jq
   ```
   - Returns `{"status":"ok"}` AND `/health/schema` 503s → **Supabase
     down.** Continue here.
   - `/` also fails → **Backend down**, see §1.

2. **Is it Supabase platform or just my project?**
   - status.supabase.com banner → platform-wide outage.
   - Supabase dashboard → your project shows the project as paused, error,
     or DB lock → project-specific issue.

3. **Is the project paused?** (Free tier auto-pauses after 7 days of
   inactivity.) If yes, the fix is one click (§2.3a).

4. **Was there a manual change in Supabase Studio recently?** RLS policy
   change, schema migration, role grant edit — could lock out the
   service-role connection.

### 2.3 Mitigation

There is **no rollback option** for managed Supabase. Options in
descending order of preference:

#### 2.3a Project paused (free tier)

Supabase dashboard → project → **Restore Project**. Single click. DB
returns in ~30s.

#### 2.3b Platform outage

Communicate to yourself / users:

```markdown
Service is degraded — our database provider (Supabase) is experiencing
an outage. Following at status.supabase.com. ETA unknown; we will
update here every 30 minutes.
```

(Until a status page exists for LDS, the above goes in your incident log
and in the Discord channel. When paying users land, this routes to a
public status page.)

**Do not migrate** databases mid-outage. The temptation to "spin up an
RDS / Neon / new project" during a 30-minute Supabase outage costs days
of reconciliation later. Migrate only if outage exceeds your data-loss
tolerance — typically 6+ hours on a customer-facing SEV-1 — and only
after dumping a clean snapshot.

#### 2.3c Project-specific lockout (manual change broke things)

If the dashboard works but the backend can't connect (e.g. you flipped
RLS on a table by accident, or rotated `SUPABASE_SERVICE_ROLE_KEY`
without updating Render):

1. Verify the key in Render `SUPABASE_SERVICE_ROLE_KEY` matches
   Supabase → Settings → API.
2. Verify RLS deny-all is still in place on `leads`, `campaigns`,
   `campaign_messages`, `orchestration_jobs` (service role bypasses, but
   a syntactically-broken policy can crash queries).
3. Run the schema-drift gate locally:
   ```bash
   SUPABASE_DATABASE_URL='<pooler-url>?sslmode=require' \
     python -m src.scripts.schema_drift_check
   ```
   If it reports unexpected policies / grants, that's your lockout.
4. Revert the offending change in Studio.

### 2.4 What to communicate to the operator

Single-operator: you ARE the operator. Internal note in the incident log
suffices.

Once you have users:

- **First hour**: ack the outage. "Database provider is degraded;
  we're tracking."
- **Per 30 min**: status update even if "no change."
- **On recovery**: "Service restored. We'll publish a post-mortem within 48h."

Discord `#alerts` is not customer-facing. Don't post user-relevant
comms there.

### 2.5 When to wait vs. migrate

| Outage duration | Action |
|---|---|
| < 30 min | Wait. Document the timeline. |
| 30 min – 2 h | Wait but prepare migration runbook (don't execute). |
| 2 h – 6 h | Operator judgement. Consider dumping a fresh `pg_dump` to S3 if not already taken in the last 24 h. |
| > 6 h | If Supabase status page says > 6 h ETA, consider Neon / RDS / Cloud SQL migration. Multi-day reconciliation post-recovery. |

The pipeline doesn't have a migration script ready today — building one
is an action item the first time you cross the 2 h threshold.

### 2.6 Post-mortem template (Supabase down)

```markdown
# YYYY-MM-DD — Supabase down

**Severity:** SEV-1
**Duration:** HH:MM UTC → HH:MM UTC (XXm)
**Detected by:** synthetic monitor / Sentry / operator manual
**Supabase outage URL:** <status.supabase.com/incidents/...>

## TL;DR
"Supabase platform outage HH:MM–HH:MM affecting our region; service
returned 503 on DB-touching endpoints for XXm. Liveness probe stayed
green."

## Timeline (UTC)
- HH:MM — Supabase status page red
- HH:MM — synthetic monitor `/health/schema` failed
- HH:MM — operator opened incident
- HH:MM — Supabase declared recovery
- HH:MM — `/health/schema` green
- HH:MM — operator declared recovery

## Root cause
"<Supabase incident summary from their post-mortem, when published>"

## What helped
- Discord 3-fail ping caught it before users complained
- The 503 vs the cached 200 on /stats absorbed some user-visible damage

## What didn't help
- Sentry showed many duplicate `httpx.ConnectError` events — should have
  rate-limited those at the SDK level

## Action items
- [ ] Subscribe to status.supabase.com webhook → Discord (instead of polling)
- [ ] Document the migration runbook (currently a stub) — Owner: <self>. Due: next major.
```

---

## 3. Gemini API outage (SEV-2)

### 3.1 Detection

- Sentry: spike of `google.genai` exceptions, `ResourceExhausted`,
  `ServiceUnavailable`, or `DeadlineExceeded`.
- AI features fail in the dashboard:
  - **AI Chat**: returns `Failed to execute the task.`
  - **Draft Outreach** / **Draft LinkedIn**: backend returns 503 or 500.
  - **Insights**: spinner forever, then error toast.
  - **Hunt jobs**: chunk-level Gemini failures; jobs flip to `failed`
    state with `last_error` mentioning Gemini.
  - **CSV upload**: column mapping fails; ingest aborts.
- Google AI Studio → your API key → Usage tab shows quota saturation
  OR errors spike.

### 3.2 Triage

1. **Is it the platform or your quota?**
   - status.cloud.google.com — banner for "Generative AI" → platform.
   - Otherwise → quota or per-key issue.

2. **Quota saturation?** AI Studio → your API key → check daily +
   per-minute requests.
   - If 95%+ of daily quota burned → quota exhaustion. Rare on free
     tier; common if a bulk hunt job ran wild.

3. **Specific model deprecated?** Less common but possible. Check the
   Gemini SDK release notes for the version in `requirements.txt`.

4. **API key revoked / expired?**
   - Sentry would show `Unauthenticated` exceptions; AI Studio would
     show the key inactive.

### 3.3 Mitigation

The pipeline has **no graceful AI fallback** today — `Gemini` is the
only LLM (see [ADR-006](../adr/006-gemini-not-openai.md)). Mitigation is
about **defending the rest of the pipeline** during the outage, not
about replacing Gemini.

#### 3.3a Stop AI-dependent operations

1. **Pause new bulk jobs.** Until Gemini recovers, do not start:
   - Bulk Deep Hunt (`POST /hunt-all`)
   - Bulk Full Pipeline (`POST /process-all` with enrich)
   - Campaign generate (`POST /campaigns/{id}/generate`)

   The Discovery + SEO audit + cursor-paginated lead browse remain
   functional ([ADR-004](../adr/004-playwright-for-discovery-aiohttp-for-audit.md):
   neither path is Gemini-backed).

2. **Let in-flight jobs fail closed.** Each chunk in
   `task_orchestrator._process_in_chunks` catches Gemini exceptions and
   marks the chunk failed; the next retry (manually or auto-retry once
   recovered) picks up cleanly via the idempotent upsert.

3. **Communicate the degradation; accept the 503s on per-click
   drafts.** Today the backend returns 503 to `/draft-outreach` and
   `/draft-linkedin` when Gemini is down — there's no queue-and-retry
   layer. The operator messages users (§3.3c) and waits.

#### 3.3b If quota-exhausted (not platform-down)

1. Check Google AI Studio for the actual quota line breached (RPM,
   TPM, daily request count).
2. Either:
   - Wait for the quota window to roll over (RPM resets per-minute;
     daily resets at midnight Pacific).
   - Upgrade the API plan in AI Studio.
3. Identify and stop the runaway producer (usually a bulk job; check
   `/orchestrator/active`).

#### 3.3c Communicate to users

```markdown
AI features (drafts, chat, insights, contact enrichment) are temporarily
unavailable due to a Gemini outage. We'll restore them as soon as
Google does. Manual workflows (CSV upload, SEO audit, lead browsing)
remain operational.
```

### 3.4 Verification

After mitigation:

1. AI Studio → API key → make a single test call (`gh workflow run`
   doesn't exist for this; use a quick curl):
   ```bash
   curl -fsS -X POST "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=$GEMINI_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"contents":[{"parts":[{"text":"say ok"}]}]}'
   ```
2. Try **AI Chat** in the dashboard with a STATUS_CHECK question ("How
   many leads?"). Should return a real answer.
3. Try **Draft Outreach** on one lead.
4. Sentry rate of `google.genai` exceptions returns to ~0.

### 3.5 Future improvements (Gemini resilience)

Today's pipeline has no graceful AI fallback ([ADR-006](../adr/006-gemini-not-openai.md):
Gemini-only). Several resilience patterns would reduce SEV-2 surface but
none are implemented yet — track here so a real outage doesn't get
mixed up with present-tense procedure:

- **Queue-and-retry for per-click drafts.** Add a `draft_retry_queue`
  table; `/draft-outreach` / `/draft-linkedin` insert into it on Gemini
  failure; a background job drains once Gemini recovers.
- **Circuit breaker.** Open after N Gemini failures in M seconds; fail
  fast for a cool-down period without burning latency budget.
- **Provider fallback.** Wire a secondary LLM (Claude / OpenAI) behind
  the same `AgenticRouter` interface. Big lift — every prompt + every
  structured-output schema would need re-tuning.

### 3.6 Post-mortem template (Gemini outage)

```markdown
# YYYY-MM-DD — Gemini API outage

**Severity:** SEV-2
**Duration:** HH:MM UTC → HH:MM UTC (XXm)
**Detected by:** Sentry `google.genai` exception spike

## TL;DR
"Gemini platform outage HH:MM–HH:MM; AI features (drafts, chat,
insights, enrichment) returned 503 for XXm. Non-AI features
(Discovery, SEO audit, browsing) remained operational."

## Timeline (UTC)
- HH:MM — first `google.genai` exception in Sentry
- HH:MM — operator opened incident
- HH:MM — paused bulk Hunt + campaign generate
- HH:MM — Gemini recovered (per cloud status)
- HH:MM — drained queued retries
- HH:MM — declared recovery

## What helped
- Sentry tag filtering on `google.genai` made it obvious within minutes
- `/orchestrator/active` showed exactly which jobs needed pausing

## What didn't
- No automatic queue-and-retry for user-initiated drafts — operator
  had to manually communicate the degradation

## Action items
- [ ] Implement queue-and-retry for `/draft-outreach` / `/draft-linkedin` —
  add `retry_queue` table or use Supabase `pg_cron`.
- [ ] Add a circuit breaker around Gemini calls (open after N failures
  in M seconds, fail fast for cool-down period).
```

---

## 4. Data corruption (SEV-1)

### 4.1 Detection

Multiple CI gates are designed to catch this fast:

- **Schema drift** (`security.yml::schema-drift`, daily + push): missing
  column, missing RLS policy, unexpected grant.
- **Referential integrity** (`security.yml::referential-integrity`,
  daily + push): orphan FK.
- **Query plans** (`security.yml::query-plans`, daily + push): missing
  hot-path index → seqscan instead of index.
- **JSONB shape** (`security.yml::jsonb-shapes`, daily): malformed
  `audit_results` or `orchestration_jobs.filters` payload.
- **NULL audit** (`security.yml::null-audit`, daily): unexpected NULL
  in a required column.
- **Orphan / zombie sweep** (`security.yml::orphans-zombies`, daily):
  soft-orphan `campaign_messages`, zombie jobs > 4 h, stuck leads > 24 h.

Manual detection:
- Operator spots a clearly-wrong row in Supabase Studio (e.g. duplicate
  `unique_key`, NULL `name`, `audit_status` outside the allowlist).
- A user-facing dashboard column shows blanks where data should be.
- A bulk export produces nonsense rows.

### 4.2 Triage

The first triage call is **scope**:

| Scope | Indicator |
|---|---|
| **One row** | Manual spot of a single bad row; CI gates green |
| **One table** | CI flags one table's invariant only |
| **One time window** | CI flags rows with `created_at` in a narrow range |
| **All tables** | Multiple CI gates red simultaneously |

Then **cause**:

| Cause | Symptom |
|---|---|
| Bad migration | Started right after `supabase_schema.sql` change; migration-safety workflow_dispatch run is suspicious |
| Runaway job | One worker writing too much; `orchestration_jobs` has a long-running entry; `audit_results` JSONB rows much larger than typical |
| Manual Studio edit | No CI run between the last green state and the drift |
| RLS / grant change | `schema-drift` is the only red gate |
| Data exfiltration / corruption attack | Cross-ref §5 (security incident); proceed both runbooks in parallel |

### 4.3 Mitigation

#### 4.3a STOP WRITES

**This is the first action. Always.** Continuing writes onto a
corrupted dataset multiplies the reconciliation cost.

**Action**: Render dashboard → backend service → Settings → **scale to
0 instances** → Save. Wait ~30 s for the existing process to terminate.

The frontend will return "backend unreachable" on every action. That's
the price of avoiding worse corruption. Communicate to users.

> **Future improvement** (not yet implemented): a `READ_ONLY_MODE=true`
> env flag would let the backend keep `GET` traffic flowing while
> short-circuiting every write endpoint to 503 — less disruptive than
> scaling to 0. Tracked in §4.5 action items.

#### 4.3b Snapshot current state

Before any restore action, snapshot the corrupted state — you may need
it for forensics or to recover values the PITR restore overwrites.

```bash
# Via Supabase Management API (replace <PROJECT_REF>):
curl -X POST -H "Authorization: Bearer $SUPABASE_ACCESS_TOKEN" \
  "https://api.supabase.com/v1/projects/<PROJECT_REF>/database/backups/restore"
# (Confirms the latest auto-backup; document its timestamp in the incident log)
```

OR `pg_dump` via the pooler:
```bash
pg_dump "$SUPABASE_DATABASE_URL?sslmode=require" \
  --format=custom \
  --no-owner --no-acl \
  --file=corrupted-state-$(date -u +%Y%m%dT%H%M%SZ).dump
```

Keep both the auto-backup pointer and the explicit pg_dump.

#### 4.3c Determine the pre-corruption timestamp

Walk back through the CI history for the first red gate:

```bash
gh run list --workflow=security.yml --limit=30 \
  --json conclusion,createdAt,name
```

The first failed `security.yml` run gives you a window. Subtract a safe
buffer (1 h) → that's your PITR restore target.

#### 4.3d PITR restore

Cross-ref: **Faza 5.15** = the PITR verification chain in
`backup-verify-deep.yml`. The workflow is **disabled by default** and runs
only via `workflow_dispatch`. Operator-only.

```
1. GitHub → Actions → backup-verify-deep → "Run workflow"
   Input: target_timestamp = <pre-corruption UTC timestamp>
2. The workflow creates a Supabase branch restored to that timestamp,
   runs schema-drift + referential-integrity + a row-count diff,
   then deletes the branch.
3. If the diff looks correct, run the actual restore via the Supabase
   API or dashboard → Project Settings → Backups → Restore → pick the
   same timestamp.
```

> **Read the row-count diff carefully** before the real restore. PITR
> restores the ENTIRE database to that timestamp; rows inserted between
> the corruption and the restore are lost. If users created legitimate
> data during that window, you need a row-level reconciliation step.

#### 4.3e Re-enable writes

After restore + verification:

1. Render dashboard → backend service → scale back to 1+ instances.
2. Probe `/` and `/health/schema`.
3. Run `gh workflow run security.yml` to confirm gates pass against the
   restored DB.
4. Discord ping the channel: "Restored from PITR @ <timestamp>. Service back."

### 4.4 Verification

1. CI security.yml runs clean (schema-drift, referential-integrity,
   query-plans, jsonb-shapes, null-audit, orphans-zombies all green).
2. Manual sample: pull 20 random rows from each core table and eyeball.
3. Synthetic monitor reports green for 30 consecutive minutes.
4. Sentry rate is baseline.

### 4.5 Post-mortem template (Data corruption)

```markdown
# YYYY-MM-DD — Data corruption

**Severity:** SEV-1
**Duration:** HH:MM UTC (detected) → HH:MM UTC (restored)
**Detected by:** <CI gate name> / operator manual / customer report

## TL;DR
"<Cause> corrupted <scope> between HH:MM and HH:MM. Restored from PITR
to <timestamp>; lost <N> rows of <legitimate user data | acceptable
side-effects | nothing>."

## Timeline (UTC)
- HH:MM — first CI gate red
- HH:MM — operator opened incident
- HH:MM — STOP WRITES (backend scaled to 0)
- HH:MM — corruption scope determined
- HH:MM — pre-corruption timestamp identified
- HH:MM — PITR test restore on Supabase branch
- HH:MM — diff approved
- HH:MM — production PITR restore initiated
- HH:MM — restore complete
- HH:MM — writes re-enabled
- HH:MM — all CI gates green

## Root cause
File:line of the bad migration / job / change.

## Data loss
- N legitimate rows lost (created between corruption and restore points)
- Reconciliation: <how the rows were recovered, or "accepted loss">

## What helped
- daily CI security.yml — caught it within 24 h of the bad change
- `backup-verify-deep` proving the PITR chain monthly meant the restore
  drill was familiar

## What didn't
- No automated STOP-WRITES toggle — had to manually scale Render to 0
- Pre-corruption timestamp window was hard to identify; CI history was
  fast to walk but not perfectly precise

## Action items
- [ ] Implement `READ_ONLY_MODE` env flag with documented behaviour:
  every write endpoint returns 503 + "service is in read-only
  recovery mode."
- [ ] Add a Supabase access-log query script — finds the offending
  write call sites for faster forensics.
- [ ] Update §4 of incidents.md if a new failure mode was observed.
```

---

## 5. Security incident (SEV-1)

Cross-ref: **Faza 8.10** = secret rotation cadence in
[`docs/secret-inventory.md`](../secret-inventory.md). Tiered:

- **Monthly**: `SUPABASE_SERVICE_ROLE_KEY`, `RENDER_API_KEY`,
  `SUPABASE_DATABASE_URL`
- **Quarterly**: `API_SECRET_KEY`, `ADMIN_TOKEN`, `GEMINI_API_KEY`

### 5.1 Detection

- **gitleaks CI** (push + daily) flags a secret committed to history.
- **container-scan** (Trivy + Grype on every build) reports a critical
  CVE in a deployed image.
- **Sentry** shows authenticated requests from unexpected IPs
  (`tag:remote_ip` outside operator's geography) — possible API key
  leak.
- **Supabase logs**: `service_role` connections from unexpected IPs.
- **Discord webhook**: messages appearing without a corresponding
  workflow run — webhook URL leaked.
- **Render**: deploy event from an unfamiliar identity (your account
  compromised).
- **Manual**: you find a secret in a Slack DM / screenshot / pastebin
  / past chat log.

### 5.2 Triage

1. **What secret? Match against
   [`docs/secret-inventory.md`](../secret-inventory.md).** Each entry
   has a "blast radius" line — read it before deciding pace.

2. **Active exploitation in evidence?** If yes → SEV-1, full
   mitigation now. If no (e.g. gitleaks caught it in a PR before
   merge) → SEV-2, rotate as scheduled.

3. **Scope of access** the secret unlocks:
   - `SUPABASE_SERVICE_ROLE_KEY` → full DB read/write, bypasses RLS
   - `API_SECRET_KEY` → backend access (rate-limited 60/min, but full
     functional scope)
   - `ADMIN_TOKEN` → adds `DELETE /leads/clear` access
   - `GEMINI_API_KEY` → Gemini quota burn (cost attack)
   - `RENDER_API_KEY` → can trigger deploys; can read env vars
   - `DISCORD_WEBHOOK_URL` → can post arbitrary content to your
     channel (annoying, not destructive)
   - GitHub PAT / GHCR token → can push images / read code (matters
     if private repo)

### 5.3 Mitigation

#### 5.3a Revoke the compromised secret immediately

| Secret | Revoke at |
|---|---|
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase → Settings → API → Reset (generates new key) |
| `API_SECRET_KEY` | Nothing to "revoke" — the secret IS the value. Rotate (next step). |
| `ADMIN_TOKEN` | Same — rotate. |
| `GEMINI_API_KEY` | AI Studio → API key → Revoke. Generate a new one. |
| `RENDER_API_KEY` | Render → Account Settings → API Keys → Delete. Generate a new one. |
| `DISCORD_WEBHOOK_URL` | Discord → Server Settings → Integrations → Webhooks → Delete. Create a new one. |
| GitHub PAT | github.com → Settings → Developer settings → Personal access tokens → Revoke. |

#### 5.3b Generate new secret + propagate

The new secret needs to land in **every place** the old one lived:

| Secret | Locations |
|---|---|
| `SUPABASE_SERVICE_ROLE_KEY` | Backend `.env`, Render backend env, `SUPABASE_DATABASE_URL` (if password component matches) |
| `API_SECRET_KEY` | Backend `.env`, Render backend env, frontend `.env.local`, Render frontend env |
| `ADMIN_TOKEN` | Same as `API_SECRET_KEY` (both backend + frontend must match) |
| `GEMINI_API_KEY` | Backend `.env`, Render backend env |
| `RENDER_API_KEY` | GitHub repo secret `RENDER_API_KEY` (used by deploy-backend.yml) |
| `DISCORD_WEBHOOK_URL` | GitHub repo secret + the synthetic-monitor's `SLACK_WEBHOOK_URL` (if that was the fallback) |

Generate cryptographically random replacements:

```bash
# 32-byte base64 (good for API_SECRET_KEY, ADMIN_TOKEN)
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

#### 5.3c Redeploy both services

After rotating, **both services need a redeploy** to pick up the new env:

```
Render dashboard → backend service → Manual Deploy → Clear cache & deploy
Render dashboard → frontend service → Manual Deploy → Clear cache & deploy
```

Wait for both to go green.

#### 5.3d Verify

1. `curl -fsS "$BACKEND_URL/" | jq` — liveness ok.
2. `curl -fsS -X POST -H "X-API-Key: <new-key>" "$BACKEND_URL/audit-status"` — auth works.
3. Sign in to the dashboard with a Supabase Auth user → all actions
   functional.
4. **Old key MUST fail**:
   ```bash
   curl -i -X POST -H "X-API-Key: <OLD-key>" "$BACKEND_URL/audit-status"
   # Expect 403 Invalid or missing API key.
   ```
5. Discord channel: confirm a workflow-dispatched test alert lands
   (via the throwaway `test-alert.yml` pattern in
   `docs/alerting.md` §verification).

#### 5.3e Audit logs

After revocation, walk back through logs to determine **how the secret
leaked and what an attacker did with it**:

- **Sentry**: filter events from the leak window (`time:>YYYY-MM-DD`)
  by `tag:request_id` or unusual `tag:user.ip`. Each event has the
  Supabase user (when available) and the route.
- **Supabase logs**: project dashboard → Logs → filter on
  `request.path` containing your domain's auth flow, or on
  `service_role` connection events from unexpected IPs.
- **Render logs**: backend service → Logs → grep the rate-limit-key
  output (logged on rate-limit trip) for non-operator IPs.
- **GitHub audit log**: org settings → Audit log → filter by actor /
  action / time.

Save the findings in the incident log.

#### 5.3f Notify

Single-operator: incident log entry. Once you have users:

- **No user data leaked**: silent rotation; document for trust-page.
- **User data leaked**: legal-mandated notification (GDPR: 72 h to
  supervisory authority; users without undue delay). Template:
  ```markdown
  Subject: Important: a security incident may have affected your data

  On YYYY-MM-DD, we became aware that <secret type> had been
  compromised between HH:MM and HH:MM UTC. During this window,
  <scope of access> was theoretically available to an unauthorized
  party.

  We have:
  - Rotated the compromised secret at HH:MM UTC.
  - Reviewed access logs and <conclusion>.
  - <Other steps taken>.

  Your data <was / was not> accessed. We are <doing X to prevent
  recurrence>. Please <action user should take, if any>.

  Questions: <contact>.
  ```

### 5.4 Verification (post-rotation)

After 24 h:

- gitleaks CI clean on `main` and on any open PR.
- Sentry: no new unauthorized-access patterns.
- Supabase: service-role connections only from Render's IP range.
- No Discord webhook posts that don't trace to a workflow run.

### 5.5 Post-mortem template (Security incident)

```markdown
# YYYY-MM-DD — Security incident: <secret type> rotation

**Severity:** SEV-1
**Duration:** HH:MM UTC (detected) → HH:MM UTC (rotation verified)
**Detected by:** gitleaks / Sentry / manual / external report

## TL;DR
"<Secret type> was exposed via <vector> between HH:MM and HH:MM.
Rotated at HH:MM. <Did / did not> find evidence of exploitation.
<User data implications, if any>."

## Timeline (UTC)
- HH:MM — leak occurred (or possible window starts)
- HH:MM — first detection signal
- HH:MM — operator opened incident
- HH:MM — secret revoked at provider
- HH:MM — new secret generated
- HH:MM — propagated to all consumers
- HH:MM — services redeployed
- HH:MM — verification (old key fails, new key works)
- HH:MM — log audit complete
- HH:MM — incident closed

## Root cause
How the secret leaked. Specific. "Committed accidentally in PR #123,
file `tests/...`, line N. Removed in PR #124 but git history still
contained it." OR "Phishing email targeting maintainer, credentials
typed into a fake provider login page." OR similar.

## Scope of access during the leak window
- <what an attacker could have done with the secret>
- Evidence of exploitation: <none | specific events>

## User data impact
- None / <specific>.

## What helped
- gitleaks daily CI (vs only on PR) — caught a direct push to main
- Single-operator simplicity — rotation propagation is 5 places, not 50

## What didn't
- Manual propagation across `.env` + Render dashboard + frontend .env
  + GitHub secrets — easy to miss one
- No automated rotation tooling

## Action items
- [ ] Add automated rotation script that updates all consumers atomically.
- [ ] Bring forward the next scheduled rotation of THIS secret class
  (this was a "monthly" secret — next rotation now scheduled YYYY-MM-DD).
- [ ] If gitleaks pattern was new, add a regression rule.
- [ ] Update `docs/secret-inventory.md` if the secret's blast radius
  changed.
- [ ] User notification sent / N/A.
```

---

## Where to file post-mortems

```
docs/runbooks/incidents/
  YYYY-MM-DD-backend-down.md
  YYYY-MM-DD-supabase-down.md
  YYYY-MM-DD-gemini-outage.md
  YYYY-MM-DD-data-corruption.md
  YYYY-MM-DD-security-<secret>.md
```

Each file:
- Uses the template from the relevant scenario above.
- Goes in via PR (even single-operator) — the PR description is the
  TL;DR, the file body is the detail. Adds a review-trail and pins the
  document into git history.
- Tagged with a GitHub label `incident` for easy filtering in `gh issue
  list` and the `incidents/` folder index.

Once the incident log accumulates 3+ entries of the same kind, look for
**systemic action items** worth elevating to an architecture change
(new ADR in `docs/adr/`).

---

## Quick-reference incident-time commands

> **First step in a fresh shell**: load env so `$BACKEND_URL` /
> `$API_SECRET_KEY` / `$SUPABASE_DATABASE_URL` are defined.
>
> ```bash
> # macOS / Linux — load .env into the current shell
> set -a; source ./.env; set +a
> echo "BACKEND_URL=$BACKEND_URL"   # sanity check
> ```
>
> If you're SSH'd in somewhere without the repo, grab the values
> directly from Render → service → Environment → reveal. The literal
> URLs are usually `https://lead-scraper-backend.onrender.com` and
> `https://lead-scraper-frontend.onrender.com` (substitute if your
> deployment uses different names).

```bash
# Backend liveness
curl -fsS -m 10 "$BACKEND_URL/" | jq

# Schema health (auth required)
curl -fsS -m 10 -H "X-API-Key: $API_SECRET_KEY" "$BACKEND_URL/health/schema" | jq

# Active orchestrator jobs
curl -fsS -m 10 -H "X-API-Key: $API_SECRET_KEY" "$BACKEND_URL/orchestrator/active" | jq

# Stop a wedged job
curl -fsS -X POST -H "X-API-Key: $API_SECRET_KEY" \
  "$BACKEND_URL/orchestrator/stop/<job_id>"

# Force-run CI invariant gates against prod DB
gh workflow run security.yml

# Force-run PITR verification
gh workflow run backup-verify-deep.yml -f target_timestamp=<UTC>

# Send a test Discord alert via throwaway workflow
# (see docs/alerting.md §verification)

# Last 30 minutes of backend logs
# Render dashboard → backend service → Logs → filter timestamp
```
