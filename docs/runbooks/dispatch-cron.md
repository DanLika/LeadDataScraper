# Dispatch tick — Render Cron setup + ops

The Phase 15.2 dispatch worker
(`src/workers/dispatch_tick.py` + `scripts/dispatch_tick.py`) is
designed to run as a **Render Cron Job**, NOT as a long-lived uvicorn
loop. Each tick is idempotent, short-lived, and crash-safe via the
stale-claim sweeper.

## Cron expression — default 5 min, upgrade path to 1 min

| Cadence | Cron expression | When |
|---|---|---|
| **5 min (default)** | `*/5 * * * *` | Phase 15.2 dogfood — low volume, max 5-min lag on `scheduled_at` |
| 1 min (high-volume) | `*/1 * * * *` | Once average `claimed` per tick crosses ~50 — see "When to tighten" below |

Why 5 min default:
- Render Cron bills per invocation; 1 min = 1440/day, most no-op, burns logs + spend.
- 2026 cold-outreach cadence (Day 1 / 3 / 7 / 14 / 21) needs minute-accuracy on send_at like a hole in the head. A 5-min skew on a Day-7 touch is invisible.
- 5 min × 60 sec per-tick budget = headroom for the dispatcher to retry a bouncy API without crossing the cron timeout.

Why NOT slower than 5 min:
- The stale-claim sweep window is `DISPATCH_CLAIM_TIMEOUT_MIN=15` (default). At 5-min cadence the sweep recovers a crashed tick within one cron cycle. Slower cron → longer recovery.

## Render setup steps

1. Render dashboard → **New** → **Cron Job**
2. **Environment**: same Python version as the backend service
   (3.10+; see `requirements.txt` lockfile)
3. **Build command**: `pip install -r requirements.txt`
4. **Cron schedule**: `*/5 * * * *`
5. **Command**: `python scripts/dispatch_tick.py`
6. **Timeout**: 60 seconds (Render hard cap; matches
   `DISPATCH_TICK_MAX_RUNTIME_SEC=50` worker-side limit + safety margin)
7. **Env vars** (link from the backend service, don't duplicate):
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `INSTANTLY_API_KEY`
   - `INSTANTLY_DEFAULT_CAMPAIGN_ID` _(or set per-call via API)_
   - `UNSUBSCRIBE_BASE_URL` — base for RFC 8058 List-Unsubscribe links (e.g. `https://lead-scraper-backend-x51l.onrender.com`). Without this, sent emails carry no unsubscribe link (AUP risk).
   - `UNSUBSCRIBE_TOKEN_SECRET` — HMAC key for unsubscribe token generation/verification.
   - `OPERATOR_NAME` — appended to outreach signature. Unset → "Your Name" placeholder.
   - `SEND_WINDOW_DEFAULT_TZ` (default `UTC`; LDS dogfood uses `Europe/Sarajevo`)
   - `DISPATCH_TICK_BATCH_SIZE=100` _(default)_
   - `DISPATCH_CLAIM_TIMEOUT_MIN=15` _(default)_
   - `DISPATCH_TICK_MAX_RUNTIME_SEC=50` _(default)_

## When to tighten the cadence

Promote `*/5 * * * *` → `*/1 * * * *` when:

- Average `claimed` per tick ≥ 50 over a rolling 24h
- OR p95 `scheduled_at → dispatched_at` lag exceeds the campaign's
  send-window precision target (rarely matters under 1h precision)
- OR Render Cron logs show 5+ ticks with `errors:["runtime_cap_*"]`
  per day (the worker is hitting the wall, not the cron interval)

## Operator monitoring

The CLI prints a single JSON line to stdout per tick:

```json
{
  "swept_stale": 0,
  "claimed": 12,
  "skipped_suppressed": 1,
  "skipped_window": 0,
  "dispatched": 11,
  "failed": 0,
  "errors": [],
  "elapsed_seconds": 1.34
}
```

Grep targets:

| Pattern | Action |
|---|---|
| `"errors":[..."db_client_unavailable"...]` | env misconfig; check `SUPABASE_URL` / `_SERVICE_ROLE_KEY` |
| `"errors":[..."dispatcher_unavailable"...]` | check `INSTANTLY_API_KEY` |
| `"swept_stale":` > 0 for 3+ consecutive ticks | worker crashing mid-dispatch; check Sentry for traceback near `claim_due_batch` |
| `"failed":` rising vs `"dispatched":` | per-lead API rejection rate climbing; usually `INSTANTLY_DEFAULT_CAMPAIGN_ID` invalid OR rate-limit on the subaccount |
| `"elapsed_seconds":` > 50 | cron about to time out; tighten `DISPATCH_TICK_BATCH_SIZE` OR widen the cron interval |

Render dashboard surfaces stdout per cron run; the daily / weekly
view shows the count of non-zero `failed` events for trend tracking.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Tick ran cleanly (incl. "no due messages") |
| 1 | Operator misconfig (DB or dispatcher unavailable) |
| 2 | Worker hit the runtime cap with errors |

Render Cron retries on non-zero exits by default; verify the Cron Job's
retry policy in the dashboard matches operator intent.

## Concurrency safety

The worker is safe under concurrent invocation (race between two cron
runs at the same minute, or between a manual `python scripts/dispatch_tick.py`
and a scheduled run):

- **Sweep stale**: `WHERE status='dispatching' AND dispatched_at < cutoff`
  — both ticks try the same UPDATE; only one matches per row (the
  other sees `status='pending'` and skips).
- **Claim**: two-phase status-transition. Both ticks SELECT the same
  due ids; only the first UPDATE's `status='pending'` predicate
  matches. Documented in
  `src/repositories/campaign_message_repo.py` module docstring.

## Crash recovery

If a tick crashes between SELECT-and-UPDATE (rare) OR between
claim-and-dispatch (more common — Render hard timeout, OOM):

1. Claimed rows stay in `status='dispatching'` with `dispatched_at`
   set to the crash time.
2. Next tick's `sweep_stale_claims` resets them to `'pending'` after
   `DISPATCH_CLAIM_TIMEOUT_MIN` (default 15 min).
3. The tick after that re-claims via `claim_due_batch` and retries
   the dispatch.

Worst-case recovery: 1 cron interval + 15 min. At 5-min cadence,
~20 min max delay on a crashed send.

## What this PR does NOT yet wire

These deferred items have placeholders in the worker so the operator
can see the structure without waiting for full Phase 15.3 / 15.4:

- **Lead-row join**: `email`, `first_name`, etc. come from the
  `leads` table via `lead_unique_key`. Phase 15.3's variant renderer
  needs the full lead row; for 15.2 the worker reads `recipient_email`
  if present on the `campaign_messages` row (legacy + manual seed
  rows), else skips.
- **Step + variant join**: the worker passes the step's stored
  send-window / send-days fields directly; the variant's
  body_template doesn't enter the dispatcher payload yet — Phase 15.3
  renders + injects into the Instantly payload.
- **Sequence advancement**: webhook → next-step row creation lands
  in Phase 15.4.

## Out of scope for this runbook

- Multi-provider routing (LinkedIn / HeyReach) — Phase 17
- Reply classifier (AI Haiku-based labels) — Phase 16
- Sequence builder UI (operator-facing CRUD) — Phase 18
