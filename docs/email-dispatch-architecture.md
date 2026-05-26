# Email dispatch — current state, target state, wiring plan

**Status:** plan-only. **Do NOT wire** until `docs/email-deliverability.md`
checklist 100% complete (DNS propagated, sender account live,
mail-tester 10/10).

This doc maps:
1. What exists today (`email_sender.py` + campaign endpoints).
2. What the dispatch loop should look like once DNS + provider go live.
3. Decision points that need operator input before wiring (scheduler
   host, SMTP-vs-API path, webhook authentication).

---

## 0 — 2026-05-25 multi-dispatcher pivot (overrides §2.1 + §5)

Original plan (PRs #281 / #286) is **Resend-warm-path-only**. This
section pins the broader 3-dispatcher target so subsequent PRs build
on the right model.

### 0.1 Channel map

| Channel | Provider | Audience | Reputation tier |
|---|---|---|---|
| Cold email outreach | **Instantly** | Unverified prospects, first-touch | Cold-sender pool, rotating IP/identity |
| Warm email outreach | **Resend** | Replied / engaged / opted-in prospects | Owned domain `mail.leaddatascraper.com` reputation |
| LinkedIn outreach | **HeyReach** | Pure LinkedIn surface, no email | Per-LinkedIn-account, not domain-bound |

Rationale: burning the owned-domain warm reputation on cold sends is
the costliest mistake. Instantly's pool absorbs cold-sender risk;
Resend keeps the warm domain pristine. HeyReach is a separate medium
(LinkedIn) — no email-domain reputation overlap.

### 0.2 Schema impact (Phase 14.1, additive, post-#286)

`email_send_ledger` + `email_suppression` from PR #286 are
**dispatcher-agnostic on the critical path** (`provider_message_id`
is opaque TEXT — Resend / Instantly / HeyReach IDs all fit). Three
forward-additive columns close the multi-dispatcher gap; none break
#286 callers:

```sql
ALTER TABLE email_send_ledger
  ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'resend';
ALTER TABLE email_send_ledger
  ADD CONSTRAINT email_send_ledger_provider_allowed
  CHECK (provider IN ('resend', 'instantly', 'smtp'));

ALTER TABLE email_suppression
  ADD COLUMN IF NOT EXISTS source TEXT;  -- nullable, forensic: which
                                          -- provider reported the bounce
```

Suppression stays **global** (a bounce on Resend MUST suppress
Instantly too — same recipient, same deliverability ground truth).
`source` is forensic only.

LinkedIn surface: HeyReach actions are NOT email. Either (a)
introduce `linkedin_send_ledger` + `linkedin_suppression` parallel
tables, or (b) rename `email_*` → `outreach_*` with a `channel`
discriminator. Decision deferred to Phase 17.2 (see below); the
rename path is breaking and needs a coordinated migration.

### 0.3 Phased PR map (uses `docs/roadmap.md` Phase numbering)

Maps onto the canonical Phase numbers in
[`docs/roadmap.md`](roadmap.md), not the internal §4 1-5 numbering
which is now historical context only. Phase 13.5 (DKIM/SPF/DMARC)
already existed in roadmap.md; Phase 14 + Phase 17 are NEW phases
added by this pivot doc.

| Phase | Scope | PRs |
|---|---|---|
| 13.5a (DNS + deliverability) | DKIM/SPF/DMARC ramp, mail-tester 10/10 | **operator action**, not a code PR |
| 13.5b (Resend warm path code) | `ResendEmailSender` + dispatcher schema | **#281, #286** (in flight) |
| 13.5c | Resend webhook handler (`POST /webhooks/resend`) | new |
| 13.5d | Resend dispatcher (Render Cron, warm queue only) | new |
| 13.5e | Operator-facing Send + suppression UI | new |
| 14.0 (Instantly cold path) | `EmailDispatcher` Protocol refactor + `provider` column on ledger | new (follow-up to #286) |
| 14.1 | `InstantlyEmailSender` + Instantly webhook handler | new |
| 14.2 | Cold/warm queue routing in dispatcher | new |
| 17.0 (HeyReach LinkedIn) | LinkedIn surface decision (parallel tables vs rename) + DDL | new |
| 17.1 | `HeyReachLinkedInSender` + HeyReach webhook handler | new |
| 17.2 | LinkedIn dispatcher + UI surface | new |

`docs/roadmap.md` is updated alongside this doc to add Phase 14 +
Phase 17 stubs and to clarify Phase 13.5's reduced (warm-only)
scope.

### 0.4 Out-of-scope clarification

§5 below historically excluded LinkedIn entirely. **Reverse that** —
HeyReach IS in scope, but as Phase 3.0+, AFTER Phase 1.x (Resend
warm path) reaches dogfood-ready status. The §5 LinkedIn paragraph
is preserved below as historical context but no longer normative.

---

## 1 — Current state

### 1.1 `src/integrations/email_sender.py`

- `EmailSenderBase` abstract base + `SMTPEmailSender` concrete impl.
- Reads from env: `SMTP_HOST` (default `smtp.gmail.com`), `SMTP_PORT`
  (587), `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` (defaults to user),
  `SMTP_FROM_NAME` (defaults to `LeadDataScraper`), `EMAIL_RATE_LIMIT`
  (10/min default).
- `send(to, subject, body, from_name=None) -> dict` — async.
- Hardening already in place:
  - Recipient regex `^[^@\s]+@[^@\s]+\.[^@\s]+\Z` (`\Z` not `$` — CRLF
    smuggling guard, locked in by `tests/test_crlf_injection.py`).
  - CRLF reject on subject + from_name before MIME header write.
  - 30s timeout via `asyncio.wait_for` around `_send_smtp` executor.
  - Per-instance bounce tracking (`bounced_emails: set`) — short-lived,
    not persisted.
  - Per-instance rate limit (sliding 60s window).
- **Not** wired to any backend handler or background job.

### 1.2 Campaign endpoints (`backend/main.py:1817-2018`)

| Method | Path | Status today |
|--------|------|--------------|
| POST   | `/campaigns` | Creates row in `campaigns`. |
| GET    | `/campaigns` | Lists. |
| GET    | `/campaigns/{id}` | Single fetch. |
| POST   | `/campaigns/{id}/generate` | Runs Gemini draft for each matching lead, INSERTs into `campaign_messages` with `status='pending'`. |
| POST   | `/campaigns/{id}/start` | Flips `campaigns.status='active'`. **Does NOT send anything.** |
| POST   | `/campaigns/{id}/pause` | Flips `campaigns.status='paused'`. |
| GET    | `/campaigns/{id}/export` | CSV export. |

### 1.3 `campaign_messages` schema

| Column | Type | Today |
|--------|------|-------|
| `id` | UUID | gen_random_uuid |
| `campaign_id` | UUID FK → campaigns ON DELETE CASCADE | |
| `lead_unique_key` | TEXT FK → leads.unique_key | |
| `channel` | TEXT | CHECK in (`email`, `linkedin`, `multi`) |
| `status` | TEXT | CHECK in (`pending`, `sent`, `delivered`, `replied`, `bounced`) — only `pending` written today |
| `subject`, `body` | TEXT | Gemini-drafted |
| `sent_at` | TIMESTAMPTZ | NULL until dispatcher fires |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

State machine invariant (CHECK pinned 2026-05-23):
`sent_at IS NULL AND status='pending'` is the only legitimate
not-yet-sent state. Setting `sent_at` and leaving `status='pending'`
is flagged by `src/scripts/check_orphans_and_zombies.py`.

### 1.4 Missing pieces

- No `/campaigns/{id}/send` endpoint.
- No scheduled dispatcher polling `status='pending'`.
- No webhook handler for Resend (or any provider) delivery events.
- No suppression list table.
- No per-domain / per-day rate-limit ledger.

---

## 2 — Target state

### 2.1 Recommended warm path: Resend HTTP API (not SMTP)

> **Pivot note (2026-05-25):** This section covers the **warm path
> only** — see §0 above for the broader 3-dispatcher model. Cold
> outreach goes through Instantly (Phase 2.0), LinkedIn through
> HeyReach (Phase 3.0). The §0 pivot does NOT change the
> warm-path recommendation: Resend HTTP API beats SMTP for the
> same reasons listed below.

Per [`docs/email-deliverability.md`](email-deliverability.md), the
**warm-path** provider is **Resend** (owned domain
`mail.leaddatascraper.com`). Two integration paths:

| | SMTP path | HTTP API path |
|---|---|---|
| Effort | ~30 min — env swap | ~half-day |
| Setup | `SMTP_HOST=smtp.resend.com` `SMTP_USER=resend` `SMTP_PASS=<api_key>` | New `ResendEmailSender(EmailSenderBase)` using `aiohttp` (already a dep) hitting `https://api.resend.com/emails` |
| Webhooks | ❌ none. Bounces visible only in Resend dashboard. | ✅ Resend → `POST /webhooks/resend` with `Svix-Signature` HMAC header. Wire `email.sent/delivered/bounced/complained/opened/clicked` events. |
| Suppression | ❌ manual — operator copies bounce list out of Resend dashboard | ✅ automatic — Resend suppresses internally; webhook surfaces the event. |
| Idempotency | ❌ — duplicate sends on retry are real risk | ✅ `Idempotency-Key` header per dispatch — Resend dedupes for 24h. |
| Send rate | ✅ SMTP rate-limit enforced server-side by Resend | ✅ HTTP 429 on rate breach |
| `email_sender.py` reuse | ✅ existing `SMTPEmailSender` works unchanged | ❌ new class needed, factory pattern via env (`EMAIL_PROVIDER=resend_api` vs `resend_smtp`) |

**Recommendation: HTTP API path.** The webhook → `campaign_messages.status`
sync is the load-bearing reason — without it, the entire `delivered/
bounced/replied` state machine on `campaign_messages` stays stuck at
`pending` even after a real send. SMTP path defers the half-day forever
and the state machine never gets used.

SMTP path stays viable as a **rollback option**: if the HTTP API
integration breaks on day N+1, env flip back to SMTP keeps outreach
going (minus webhook events).

### 2.2 Dispatcher loop

Polling job, runs every 5 min:

```python
# pseudocode — actual location decided in §3
async def dispatch_pending_messages():
    rows = await db.client.from_("campaign_messages") \
        .select("id, campaign_id, lead_unique_key, subject, body, channel") \
        .eq("status", "pending") \
        .in_("campaign_id", active_campaign_ids()) \
        .limit(50) \  # per-batch cap
        .execute()

    for row in rows.data:
        if not under_rate_limit(row.lead_unique_key):
            continue  # leave pending for next tick
        if in_suppression_list(lead.email):
            await mark_bounced(row.id, reason="suppression")
            continue
        try:
            result = await sender.send(
                to=lead.email,
                subject=row.subject,
                body=row.body,
                idempotency_key=f"campaign-msg-{row.id}",
            )
            await mark_sent(row.id, resend_message_id=result["id"])
        except RateLimitedError:
            break  # leave the rest pending
        except PermanentFailureError as exc:
            await mark_bounced(row.id, reason=str(exc))
```

Key constraints:
- **Per-batch cap = 50** (matches Resend free-tier 100/day default;
  generous for Pro). One batch every 5 min = ≤600/day in worst case.
- **Per-recipient-domain throttle = 3/hr** (defense against
  appearing-bursty to mailbox providers — Gmail/Outlook penalize
  same-domain bursts). New table `email_send_ledger(domain TEXT,
  sent_at TIMESTAMPTZ)` indexed on `(domain, sent_at DESC)`.
- **Per-day global cap = 50** at dogfood phase (operator manual
  curation expected). Set via env `MAX_OUTREACH_PER_DAY`.
- **Off-hours pause**: skip dispatch outside 09:00–18:00 Europe/Sarajevo
  (operator timezone). Cold outreach landing at 03:00 local recipient
  time signals automation; 10:00 local landing signals human.

### 2.3 Webhook handler

New endpoint:

```
POST /webhooks/resend
  Headers:
    Svix-Id: msg_<id>
    Svix-Timestamp: <unix>
    Svix-Signature: v1,<base64>
```

- **Authentication**: HMAC-SHA256 verify against `RESEND_WEBHOOK_SECRET`
  env using `secrets.compare_digest`. No X-API-Key gate (Resend's POST
  origin can't carry it).
- **No CORS** — webhooks are server-to-server.
- **Replay protection**: reject if `Svix-Timestamp` is > 5 min in past
  or future.
- **Body validation**: Pydantic model with `Literal["email.sent",
  "email.delivered", "email.delivery_delayed", "email.complained",
  "email.bounced", "email.opened", "email.clicked"]` + bounded data
  fields.
- **Idempotency**: use `Svix-Id` as dedup key in a small TTL set.
- **Side effect**: UPDATE `campaign_messages` by `resend_message_id`
  (new column to add) → set new `status` per event type:
  - `email.sent` → already `'sent'` (no-op except `sent_at` ack).
  - `email.delivered` → `'delivered'`.
  - `email.bounced` → `'bounced'` + add to suppression list.
  - `email.complained` → `'bounced'` + suppression + flag the
    campaign for operator review.
  - `email.opened`, `email.clicked` — out of scope for dogfood;
    log and discard (operator can read Resend dashboard for now).

### 2.4 Schema additions needed

Migration (separate PR, after dispatcher is ready to wire):

```sql
ALTER TABLE campaign_messages
  ADD COLUMN provider_message_id TEXT,
  ADD COLUMN bounce_reason TEXT;

CREATE INDEX idx_campaign_messages_provider_message_id
  ON campaign_messages(provider_message_id)
  WHERE provider_message_id IS NOT NULL;

CREATE TABLE email_send_ledger (
  id BIGSERIAL PRIMARY KEY,
  recipient_domain TEXT NOT NULL,
  sent_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_email_send_ledger_domain_sent
  ON email_send_ledger(recipient_domain, sent_at DESC);

-- Phase 14.2: renamed from email_suppression to a generic multi-channel
-- table. identifier_type ∈ {email, domain, linkedin_url, phone}, channel ∈
-- {email, linkedin, sms, all}. Dispatcher predicate filters on
-- (identifier_type='email', channel ∈ {email, all}).
CREATE TABLE suppressions (
  id BIGSERIAL PRIMARY KEY,
  identifier_type TEXT NOT NULL DEFAULT 'email',
  identifier_value TEXT NOT NULL,
  reason TEXT NOT NULL,  -- 'bounce' / 'bounce_hard' / 'bounce_soft_3x' /
                         -- 'complaint' / 'manual' / 'unsubscribe' /
                         -- 'gdpr_request' / 'spam_trap'
  channel TEXT NOT NULL DEFAULT 'email',
  source_provider TEXT,         -- 'resend' / 'instantly' / 'smtp' / 'heyreach' / 'manual'
  source_campaign_id UUID REFERENCES campaigns(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT,
  notes TEXT,
  UNIQUE (identifier_type, identifier_value, channel)
);
CREATE INDEX idx_suppressions_lookup
  ON suppressions (identifier_value, channel)
  WHERE channel IN ('email', 'all');

ALTER TABLE email_send_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE suppressions ENABLE ROW LEVEL SECURITY;
-- + deny-all policies matching the 5-table pattern, + GRANT REVOKE
-- on anon/authenticated, + schema_drift_check.py TABLES tuple update.
```

**Repository layer (Phase 14.2):** `src/repositories/suppression_repo.py`
provides `is_suppressed()`, `filter_suppressed()` (single batch query),
`add()` (duplicate-tolerant via DB UNIQUE constraint), and
`bulk_import()` (operator paste-500-emails workflow via PostgREST
`upsert(ignore_duplicates=True)`). Dispatcher precheck is unchanged
behaviour — just rewired to the new table + columns.

### Phase 14.2 PR β — thread/idempotency columns + RFC 8058 + daily-cap env

Three additive columns on `campaign_messages` (each backed by a partial
index on its only lookup shape):

- `thread_id TEXT` — provider-side conversation key. Webhook handler
  (PR γ) attributes `email_replied` events back to the originating
  thread via this column.
- `in_reply_to_message_id TEXT` — points at a previously-sent message
  in the same thread; NULL on the initial touch.
- `tracking_id UUID DEFAULT gen_random_uuid()` — LDS-side opaque
  identifier used by the RFC 8058 List-Unsubscribe-Post token. UNIQUE.

**RFC 8058 unsubscribe endpoint** (`backend/main.py`):

- `GET /unsubscribe/{token}` — minimal HTML confirmation page with a
  POST form. Public, no API key, 10/min/IP throttle.
- `POST /unsubscribe/{token}` — verify HMAC + timestamp window,
  dereference `tracking_id → campaign_messages → leads.email`, insert
  suppression(`identifier_type='email'`, `channel='all'`,
  `reason='unsubscribe'`, `source_campaign_id`). Always 200 on
  success / 410 on any failure (generic body — never leaks which
  verification stage rejected).

Token format (`src/utils/unsubscribe_tokens.py`):
- 2-byte version `v1` + 32-byte payload (`tracking_id u128 BE` +
  `issued_at u32 BE` + 12 reserved bytes) + 32-byte HMAC-SHA256
- Total raw 66 bytes → 88-char URL-safe base64 (no padding)
- 90-day TTL (Mailgun/Mailmodo recommendation)
- ±5-min clock-skew tolerance
- `hmac.compare_digest` everywhere — timing-safe

**Daily cap + send window** (`src/utils/dispatch_policy.py`):

| Env var | Default | Purpose |
|---|---|---|
| `EMAIL_DAILY_CAP_PER_MAILBOX` | `30` | Per-mailbox cap once warmed |
| `EMAIL_WARMUP_PER_MAILBOX` | `10` | Per-mailbox cap during 21-day warm-up |
| `SEND_WINDOW_START` | `09:00` | HH:MM, inclusive |
| `SEND_WINDOW_END` | `17:00` | HH:MM, exclusive |
| `SEND_DAYS` | `mon,tue,wed,thu,fri` | Comma-separated tokens |
| `SEND_TIMEZONE_MODE` | `lead` | `lead` / `campaign` / `UTC` |
| `UNSUBSCRIBE_TOKEN_SECRET` | _(required)_ | HMAC signing key for token mint/verify |
| `UNSUBSCRIBE_BASE_URL` | _(operator-set)_ | Base URL embedded in the List-Unsubscribe header |

`DispatchPolicy` is loaded **once at process boot** from `os.environ`;
test code can rebuild via `_reload_for_testing()`. Multi-worker uvicorn
races mid-request env mutation, so the snapshot is the only legitimate
read-path.

**Instantly payload integration** (`src/integrations/instantly_models.py`):
`from_lds_lead()` now accepts `list_unsubscribe=...` and threads it through
two new custom variables (`list_unsubscribe`, `list_unsubscribe_post`).
Instantly's custom-vars-to-header bridge writes them as the SMTP
`List-Unsubscribe` and `List-Unsubscribe-Post` headers.

Full wiring (dispatcher mints the token at push time using the freshly
inserted `campaign_messages.tracking_id`) lands in PR γ alongside the
webhook handler.

---

## 3 — Decision: where does the dispatcher run?

Three options. Operator picks before wiring lands.

### Option A — FastAPI `BackgroundTasks` triggered by `asyncio` loop

```python
# in backend/main.py lifespan
async def dispatch_loop():
    while True:
        await asyncio.sleep(300)
        try:
            await dispatch_pending_messages()
        except Exception:
            logger.exception("dispatch loop tick failed")

@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(dispatch_loop())
    yield
    task.cancel()
```

| Pros | Cons |
|------|------|
| Zero new infra. | Tied to uvicorn lifecycle — sleeping Render free tier = no dispatch. |
| Trivially observable (Sentry, structured logs, request_id). | Multi-worker: every worker runs the loop → 4× sends on `--workers 4`. Need leader election (advisory lock on Postgres). |
| Easy local dev. | Restarts drop in-flight messages (mitigated by idempotency keys). |

### Option B — Render Cron Job

Render supports cron jobs as a separate service running a Docker
image. Run `python -m src.scripts.dispatch_pending_messages` every
5 min.

| Pros | Cons |
|------|------|
| Decoupled from web service — no leader election issue, no sleep-tier worry (Cron tier always wakes). | Render Cron is a separate $1+/mo service. |
| Render UI shows run history + failures. | Cold-start delay (~30s) every 5 min — ledger writes must be idempotent. |
| Easy to scale to multiple cron schedules (daily report, weekly cleanup). | One more deploy target in `render.yaml`. |

### Option C — Supabase `pg_cron`

Supabase Pro plan ships `pg_cron`. Schedule a SQL function that calls a
Supabase Edge Function which calls back into the FastAPI dispatch
endpoint.

| Pros | Cons |
|------|------|
| Survives backend outage — runs in DB. | Two extra hops (cron → edge → backend) = three failure modes. |
| No new service to deploy. | LDS isn't using Edge Functions today — first one is a non-trivial onboarding tax. |
| Already on Supabase Pro for storage/backup tier — no extra cost. | Auth between Edge Function and FastAPI needs a shared secret. |

**Recommendation: Option B (Render Cron).** Render is already the
deploy target (`render.yaml`), Cron jobs are first-party + observable
in the same dashboard, and decoupling from uvicorn's lifecycle avoids
the multi-worker + sleep-tier traps in Option A. The $1+/mo cost is
trivial.

Option A is viable for **local dev** — run the loop in the uvicorn
process by default, gated by `EMAIL_DISPATCH_LOCAL=1` env so it
doesn't double-run in production where Render Cron owns it.

---

## 4 — Phase plan

This work happens **after** `docs/email-deliverability.md` checklist
is 100% complete:

```
[ ] DNS records green, mail-tester 10/10, real inbox seed-test passes
[ ] Resend account on Pro plan, RESEND_API_KEY in backend .env

→ PR 1: ResendEmailSender HTTP API client
    - src/integrations/email_sender.py: ResendEmailSender class
    - Factory selects via EMAIL_PROVIDER env (smtp / resend_api)
    - Smoke test against Resend "test" address
    - Idempotency-Key on every send

→ PR 2: Schema additions
    - provider_message_id + bounce_reason on campaign_messages
    - email_send_ledger + suppressions tables (+RLS, +grants)
    - (Phase 14.2) suppressions renamed from email_suppression, extended
      to multi-channel identifier_type + channel
    - schema_drift_check.py + check_grants_matrix.py allowlist updates

→ PR 3: Webhook handler
    - POST /webhooks/resend with Svix-Signature verify
    - Replay-window check
    - Pydantic event model
    - Update campaign_messages.status by provider_message_id
    - Suppression list inserts

→ PR 4: Dispatcher (Render Cron)
    - src/scripts/dispatch_pending_messages.py
    - render.yaml cron service definition
    - Per-domain throttle + per-day cap + off-hours gate
    - Suppression check before send

→ PR 5: Operator-facing
    - /campaigns/{id}/send endpoint (manual single-shot for testing)
    - Frontend "Send Now" button on campaigns page (X-Admin-Token gate)
    - Suppression-list view in Settings
```

Each PR ships independently — PRs 1-4 are dark-launched (no UI), only
PR 5 surfaces sending to operator.

---

## 5 — Out of scope for dogfood

- ~~LinkedIn outreach via channel `'linkedin'`~~ — **Superseded by
  §0.3 Phase 3.0 (HeyReach LinkedIn)**. Original concern (no API
  token, aggressive detection) is addressed by HeyReach acting as the
  managed-account intermediary. Keep the `channel='linkedin'` column;
  surface stays operator-curated until Phase 3.x lands.
- Open/click tracking pixels — privacy-hostile and signal value at
  dogfood scale is zero. Resend tracks at-provider; the operator can
  read it there.
- A/B subject testing.
- Reply detection (would need MX → inbound parser; out of scope per
  deliverability doc §1.5).
