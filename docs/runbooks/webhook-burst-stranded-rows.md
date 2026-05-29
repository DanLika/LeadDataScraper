# Webhook burst → stranded `webhook_events` rows

**Status**: RESOLVED. Multiple PRs:
- #357 — Path B+C (replay protection + idempotency) — 2026-05-27
- #361 — Path A sweeper cron — 2026-05-27
- #364 — import hotfix (sweeper crashed on missing module) — 2026-05-27
- **#415** — sweeper PEP-562 prime (`d922b334`, **2026-05-29T14:34:51Z**) —
  supersedes the never-merged #394 referenced in earlier session memos.
  See [pep562-cron-path-trap](./pep562-cron-path-trap.md) for the canonical
  fix description.

## Symptom

Stress test against prod `/webhooks/instantly`:
- 10 parallel POSTs with distinct `event_id` values.
- 8–23 % return HTTP **500** with body `{"detail": "internal error"}`.
- Subsequent `SELECT * FROM webhook_events WHERE processed_at IS NULL` shows
  EVERY ostensibly-500ed row PRESENT in the table.
- Row committed before the 500 returned → response code lied about durability.
- No retry from Instantly because they treat 500 as "we crashed, don't replay".
- Affected message states (`campaign_messages.status`) never advance.

In long-running prod (post-2026-05-27): same `webhook_events` rows reappear
every 2 min for hours. `processed_at` stays NULL. See
[pep562-cron-path-trap](./pep562-cron-path-trap.md) for the sweeper-tick
failure mode.

## Root cause

`POST /webhooks/instantly` originally did:

1. INSERT row into `webhook_events` (committed).
2. Schedule background task `_process_instantly_event(row.id)`.
3. Return 202 on success / 500 on background-schedule exception.

Under concurrent burst:
- Step 1 succeeded for all 10 (DB handles concurrent INSERT fine).
- Step 2 raised on subset — `asyncio.create_task` against a closing event
  loop, or transient PostgREST hiccup during row-id retrieval.
- Step 3 returned 500 → handler exited.
- Step 2 never re-fired. Row stranded with `processed_at IS NULL`.
- No sweeper existed → rows accumulated indefinitely.

**Additional cause discovered post-#361**: sweeper deployed but
PEP-562-trap-broken (see related runbook). Sweeper "ran" green ticks but
its NameError-swallowing tick produced zero successful row processing for
6+ hours.

## Fix recipe

### Three independent paths (now all live)

**Path A — sweeper cron** (PR #361):

`src/workers/webhook_sweeper.py` runs every 2 min via Render cron. Picks
unprocessed rows (`processed_at IS NULL AND created_at < now() - interval
'30 sec'`). Calls `_process_instantly_event` per row. Stamps `processed_at`
on success. Hard cap 50 rows per tick to avoid runaway.

**Path B — replay protection** (PR #357):

`/webhooks/instantly` handler wraps body validation + INSERT + background
schedule in a single `try` block. On ANY exception inside the try block,
emits a `200 OK` to Instantly (so they don't suppress retries) AND inserts
a `webhook_events` row with `processed_at=NULL`. Sweeper picks it up next
tick.

**Path C — `event_id` idempotency** (PR #357):

UNIQUE constraint on `webhook_events.event_id` from upstream Instantly.
INSERT uses `ON CONFLICT (event_id) DO NOTHING`. Webhook handler can
re-fire same `event_id` without duplicating. Sweeper guarded against
double-process by `processed_at` non-NULL guard.

### Verify post-fix

```bash
# 1. Confirm sweeper deployed and ticking
curl -sS -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/services/$WEBHOOK_SWEEPER_CRON_ID/jobs?limit=5" \
  | jq '.[].job | {ts: .startedAt, status: .status}'
# Expected: alternating successful runs every 2 min

# 2. Confirm no stranded rows
# (Run via Supabase Studio SQL Editor)
SELECT count(*) FROM webhook_events WHERE processed_at IS NULL
  AND created_at < now() - interval '5 min';
# Expected: 0

# 3. Burst stress test (300 events)
for i in $(seq 1 300); do
  curl -sS -X POST https://lead-scraper-backend-x51l.onrender.com/webhooks/instantly \
    -H "Content-Type: application/json" \
    -H "X-Webhook-Signature: $(python3 -c "...HMAC...")" \
    -d "{\"event_id\":\"stress-$i\",\"event_type\":\"open\"}" &
done; wait
# Expected: 0 stranded after 5 min sweep
```

## Spec-drift cheat sheet for future Instantly tests

10 fields to verify in ANY new `_process_instantly_event` test:

1. `event_type` — Instantly emits `email_sent`, `email_opened`,
   `email_link_clicked`, `email_bounced`, `email_unsubscribed`. NOT
   `bounce`, `click`, `open`.
2. `event_id` — Instantly UUID. UNIQUE constraint on `webhook_events`.
3. `provider_msg_id` — Instantly internal message UUID, matches our
   `campaign_messages.provider_msg_id` on dispatch.
4. `lds_message_id` — our `campaign_messages.id`, echoed back in webhook.
5. `recipient_email` — recipient address. CRLF-scrubbed at ingress.
6. `bounce_type` — `hard` / `soft`. Soft bounces are noop until
   3-strike (see PR #358).
7. `bounce_reason` — provider-supplied free text. Stripped > 200 chars +
   CRLF-scrubbed.
8. `payload->>bounce_type` — JSON path used by soft-bounce 30d window
   query.
9. `processed_at` — sweeper's only success marker. NULL = unprocessed.
10. `created_at` — sweeper waits 30 s after create before claiming, so
    handler has chance to finish first.

## Secondary finding (separate PR scope)

PR #368 tracks `_process_instantly_event` handler-side transport — the
HMAC verify currently happens BEFORE body parse. A 60+ KB Instantly burst
can saturate the HMAC verifier before slowapi rate-limit fires. Slated for
Phase 16. Not blocking.

## Recurrence guard

- **`tests/test_webhook_burst.py`** (PR #357) — 300-event burst + replay +
  adversarial all PASS. Pins handler against the original failure mode.
- **`tests/test_webhook_cron_pep562.py`** (PR #394) — subprocess-isolated
  prime test. See [pep562-cron-path-trap](./pep562-cron-path-trap.md).
- **Sweeper cron health** — Render dashboard has the cron under
  `webhook-sweeper`. Job ID + team owner ID pinned in
  [render_cron_deploy_recipe memory](./README.md#render-cron-deploys).

## Related

- Memory: `bug_webhook_burst_stranded_rows_2026-05-27.md`,
  `session_2026-05-27_webhook_stress_test.md`,
  `session_2026-05-27_webhook_pr_race.md`,
  `session_2026-05-28_final_arc.md`
- PR: #357 (Path B+C), #361 (Path A sweeper), #364 (import hotfix),
  #394 (PEP-562 prime)
- Code: `backend/main.py:_process_instantly_event`,
  `src/workers/webhook_sweeper.py`
- Related runbooks: [pep562-cron-path-trap](./pep562-cron-path-trap.md),
  [dispatch-cron.md](./dispatch-cron.md)
