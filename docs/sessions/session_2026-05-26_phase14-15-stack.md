# Session 2026-05-26 — Phase 14+15 stack ready, operator-blocked

8 DRAFT PRs stacked from `main`, ~9300 LOC, 289 unit tests green.
Awaiting operator action to unblock the merge sequence + provision
the runtime + cron + DNS prerequisites before the first live cold
send.

## Status

| Phase | PRs | LOC | Tests |
|---|---|---|---|
| 14 dispatch hardening (close-out) | #320 → #324 | ~4000 | 106 |
| 15 sequencing engine | #325 → #328 | ~5300 | 289 |
| **Total** | **8 DRAFT PRs** | **~9300 LOC** | **289 green** |

### Stack chain

```
main ← #320 ← #321 ← #322 ← #323 ← #324 ← #325 ← #326 ← #327 ← #328
       14.1   14.2α  14.2β  14.2γ   14.3   15.1   15.2   15.3   15.4
```

Each PR base = the previous PR's branch. When `main` advances, the
stack rebases from the bottom up (GitHub auto-rebases on merge of
the base PR).

## Operator unblock backlog

### Priority 1 — blocks entire stack from merging

- [ ] `SUPABASE_DATABASE_URL` secret restore (issue #305) — CI runner
      outage gates all 8 PRs

### Priority 2 — required for first live cold send (do AFTER P1)

- [ ] DNS records `mail.leaddatascraper.com` (SPF + DKIM + DMARC,
      `p=none` start)
- [ ] mail-tester.com 10/10 verification
- [ ] Resend domain verify in dashboard

### Priority 3 — Render env vars (set ALL before scheduling cron)

- [ ] `INSTANTLY_API_KEY`
- [ ] `INSTANTLY_DEFAULT_CAMPAIGN_ID`
- [ ] `INSTANTLY_WEBHOOK_SIGNING_SECRET` (from Instantly dashboard →
      Webhooks)
- [ ] `UNSUBSCRIBE_TOKEN_SECRET` (generate 32+ char random)
- [ ] `UNSUBSCRIBE_BASE_URL` (e.g. `https://lds.leaddatascraper.com`)
- [ ] `OPERATOR_NAME`
- [ ] `OPERATOR_SIGNATURE`
- [ ] `SEND_WINDOW_DEFAULT_TZ=Europe/Sarajevo`

### Priority 4 — Instantly dashboard config

- [ ] Webhook URL: `https://lead-scraper-backend-x51l.onrender.com/webhooks/instantly`
- [ ] Enable events: `email_sent`, `email_bounced`,
      `email_unsubscribed`, `email_replied`
- [ ] Test webhook signature in Instantly UI

### Priority 5 — pre-merge codebase prep

- [ ] `make lock-python` (jinja2 added in #327; `lockfile-sync` CI gate)

### Priority 6 — schema apply (idempotent ALTER chain, safe to rerun)

- [ ] Apply `supabase_schema.sql` to live Supabase via
      `supabase db push` OR `psql`

### Priority 7 — Render Cron job

- [ ] Create cron: schedule `*/5 * * * *`, command
      `python scripts/dispatch_tick.py`
- [ ] Timeout 60s, concurrency 1
- [ ] Follow [`docs/runbooks/dispatch-cron.md`](../runbooks/dispatch-cron.md)

## Merge sequence (after P1 resolves)

1. #320 (Instantly dispatcher Phase 14.1)
2. #321 (suppressions α)
3. #322 (thread cols + RFC 8058 β)
4. #323 (webhook + HMAC γ)
5. #324 (round-trip + real impl close)
6. #325 (sequencing schema)
7. #326 (dispatch tick)
8. #327 (renderer + thread + N+1)
9. #328 (webhook advancement)

Each merge:
```
gh pr merge <n> --squash --delete-branch
```
after CI green. Branch protection allows `--admin` override only as
last resort (P0 risk).

## First live send checklist (after merges + P2-P7 done)

1. Manually create test sequence via SQL/PostgREST: 3 steps, days
   1/3/7, single variant each
2. Add 1 test lead (your own throwaway email) to `leads` table
3. Dispatch tick runs (cron `*/5`) → first step sent
4. Confirm in Instantly dashboard + your inbox
5. Reply from inbox → confirm webhook fires +
   `campaign_messages.status='replied'` + pending step 2 cancelled
6. Repeat with bounce (deliberately bad email) → confirm bounce flow
   + suppression insert
7. Repeat with unsubscribe link click → confirm RFC 8058 endpoint +
   `suppression(channel='all')`

## Phase backlog (deferred, design known)

- **Phase 16** — Reply classifier (Claude Haiku, 11 categories, OOO
  defer-resume).
  Reason deferred: spec benefits from real reply samples from Day 1
  dogfood.
- **Phase 17** — HeyReach LinkedIn dispatcher (parallel structure
  Phase 14.1/14.2).
- **Phase 18** — AI personalization research → write → judge loop
  (replace single-shot).
- **Phase 19** — Operator UI for sequence builder, master inbox,
  GDPR LIA tooling.

## Memory state

Wrote this session (across the two-day Phase 14+15 push):

- `memory/feedback_postgrest_only_no_raw_sql.md` — Phase 14.3 lesson
  (specs MUST mandate PostgREST chain API; never raw SQL)
- `memory/phase_15_dispatch_tick.md` — 10 canonical invariants + 3
  bonus for any future worker/template/webhook code
- `memory/session_2026-05-25_phase14_dispatch_hardening.md` —
  Phase 14 stack reference (foundation for Phase 15)
- `memory/session_2026-05-26_phase14-15-stack.md` — pointer to this
  doc

`MEMORY.md` index updated with all four entries.

## Rough ETA estimates

| Step | Active | Wall |
|---|---|---|
| P1 (CI restore) | ~30 min | once operator looks at GH Actions billing |
| P2 (DNS) | 1-2 h | + 24 h propagation |
| P3 (env vars) | 30 min | — |
| P4 (Instantly dashboard) | 15 min | — |
| P5 (lockfile) | 5 min | — |
| P6 (schema apply) | 5 min | — |
| P7 (Render cron) | 10 min | — |
| Merge sequence | ~30 min sequential `--squash` | — |

**Total to first live send: ~half day active + 1 overnight DNS wait**

## Known gaps / tech debt

- Schema migration via `supabase_schema.sql` append, not
  `supabase/migrations/` — works but not best practice. Move to
  `migrations/` as separate refactor PR after Phase 16.
- `leads.timezone` column doesn't exist; `SEND_WINDOW_DEFAULT_TZ`
  env used as fallback. Geocoding pass to populate per-lead TZ
  deferred to Phase 19.
- Cancellation race on `'dispatching'` status documented but not
  solved (belt-and-braces via Instantly server-side reply pause +
  suppression-table redelivery block). 2PC infrastructure required
  to fully eliminate.
- Issue #304 (`schema_drift_check` missing `account_deletions`
  assertion) still open.

## DO NOT MERGE PRE-OPERATOR

- **Don't admin-merge any of #320-#328 without CI green.** Stack
  depth = high regression risk if any middle PR has a hidden issue.
- **Exception:** #324 (Phase 14.3) advisor caught a P0 before merge
  (bulk-stamp footgun in `_instantly_handle_sent`). The same gate
  must apply to live deploy — manually verify the state machine
  end-to-end on one test lead before opening the cron gate.

## End session

8 PRs ready for review + merge. Operator backlog is the only
blocker; once P1 clears the rest cascades. Next session can pick
up at Phase 16 (reply classifier) — defer until real dogfood reply
samples land.

## Validation findings (2026-05-26 sim run, sha fb7aae8)

- pytest 1029 pass, 0 fail on merged state
- Ruff +19 errors (101 vs 90 ceiling) → fix-up PR #330 opened on stack
- Mypy +57 on top of pre-existing +205 main drift (CI ratchet dormant during #305 outage)
- No merge conflicts, no drift gate violations, CLI smoke clean
- Verdict: stack ships clean after ruff fix-up; mypy reconciliation = separate post-#305 work item
