# Dispatch cron `no_email_or_lead_row` (stale deploy / env drift)

**Status**: UNRESOLVED root-cause. Local repro never hits it; only Render-side
dispatch produces the bug. Working hypothesis: stale deploy on the Render
cron service, or environment-variable drift between backend service and cron
service. Operator action: redeploy cron service from latest main; if recurs,
diff env-vars between services.

## Symptom

After Phase 14+15 prod ship, dispatch tick on Render runs every minute. Most
days the queue is empty. When dispatch finds a queued `campaign_message`:

- Render cron `dispatch-tick` logs: `bounce_reason=no_email_or_lead_row`.
- Row's `campaign_messages.status` flips to `failed`.
- No email goes out. No further retry — `failed` is terminal.
- Lead row exists, has valid `email`. Sequence step exists. Variant exists.

**Critical distinguisher**: local `dispatch_tick.py` against same prod DB
fetches lead row cleanly. ONLY Render-side execution fails.

## Root cause (HYPOTHESIS — not confirmed)

`dispatch_tick.py` calls `lead_repo.fetch_many(unique_keys)`. Repo SELECT
returns empty when:

1. **Stale deploy** — Render cron service still running pre-`leads.last_name`
   commit. `_LEAD_FIELDS` references `last_name`; PostgREST 42703 column-not-
   found error on the SELECT. Repo catches PostgrestAPIError as
   "no rows returned" → handler marks message `failed`.

2. **Env-var drift** — cron service `SUPABASE_URL` or
   `SUPABASE_SERVICE_ROLE_KEY` differs from backend service. SELECT hits
   wrong project / wrong-role authentication failure → empty result set.

3. **Time-zone gap** — cron container shell timezone differs from backend
   container. Some date-bounded filter (`created_at > now() - interval`)
   slips past expected windows. Less likely given the failure mode (entire
   row gone, not specific date filtering), but worth verifying.

## Diagnostic recipe

```bash
# 1. Confirm cron service is on latest commit
curl -sS -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/services?type=cron_job&name=dispatch-tick" \
  | jq '.[].service | {id, name, branch, commit}'
# Compare commit to current main:
git rev-parse origin/main

# 2. Diff env-vars between backend and dispatch-tick cron
for SVC in srv-d89bisbbc2fs73f1pjpg <dispatch-cron-id>; do
  echo "=== $SVC ==="
  curl -sS -H "Authorization: Bearer $RENDER_API_KEY" \
    "https://api.render.com/v1/services/$SVC/env-vars" \
    | jq -r '.[].envVar | "\(.key)=\(.value | tostring | .[0:12])..."' \
    | sort
done | diff <(grep srv-d89 ...) <(grep dispatch-cron ...)
# Look for SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / DATABASE_URL drift

# 3. Probe the same lead from Render console (Render → cron service → Shell)
python3 -c "
from src.repositories.lead_repo import fetch_many
import asyncio
print(asyncio.run(fetch_many(['<the_failing_lead_unique_key>'])))
"
# Expected: 1 row dict with `email` and `last_name` keys present.
```

## Fix recipe

```bash
# Path A — force redeploy from latest main (most likely fix)
curl -sS -X POST \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H "Content-Type: application/json" \
  "https://api.render.com/v1/services/<dispatch-cron-id>/deploys" \
  -d '{"clearCache":"do_not_clear"}'
# {"clearCache":"do_not_clear"} payload is string-enum not boolean.
# Env-PUT also auto-redeploys (alternative trigger).

# Path B — if env-vars drift, sync from backend
# (Pull backend SUPABASE_* and PUT onto dispatch cron service)

# Reset stranded `failed` rows to retry once
# (Supabase Studio SQL — careful, requires operator confirmation)
UPDATE campaign_messages
SET status = 'queued', bounce_reason = NULL, attempts = attempts + 1
WHERE bounce_reason = 'no_email_or_lead_row'
  AND status = 'failed'
  AND attempts < 3;
```

## Recurrence guard

- **Pin cron service commit at deploy** — when shipping a backend change that
  affects `lead_repo` or `dispatch_tick`, ALWAYS redeploy the cron service
  too. Render dashboard → `dispatch-tick` cron → "Manual Deploy → Deploy latest commit".
- **Env-var matrix CI** (NOT yet wired) — would query Management API for
  every service's env-vars, assert `SUPABASE_URL` and
  `SUPABASE_SERVICE_ROLE_KEY` match across backend + dispatch-tick + sweeper.
  Fail PR if drift detected.
- **Probe gate in dispatch_tick** (NOT yet wired) — on `no_email_or_lead_row`,
  emit a structured log with `lead_unique_key` AND issue a raw
  `lead_repo.fetch_many([k])` re-probe; if probe returns rows, escalate
  log level to ERROR + Sentry capture. Distinguishes "lead actually
  missing" from "repo can't see it".

## Related

- Memory: `bug_dispatch_cron_no_email_or_lead_2026-05-27.md`,
  `bug_constraint_apostrophe_double_escape_2026-05-27.md` (the
  `leads.last_name` discovery context)
- Code: `src/workers/dispatch_tick.py`, `src/repositories/lead_repo.py`,
  `backend/main.py:_LEAD_FIELDS`
- Related runbook: [check-constraint-apostrophe-drift](./check-constraint-apostrophe-drift.md),
  [env-var-local-vs-prod-drift](./env-var-local-vs-prod-drift.md)
