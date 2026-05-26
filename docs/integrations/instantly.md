# Instantly cold-outreach dispatcher

**Phase 14.1 — bulk-push only.** Webhook handler + suppression
auto-sync ship in Phase 14.2; provider-aware retry in Phase 14.3.

`InstantlyDispatcher` is the Resend AUP fallback path for **cold**
outreach. Resend's Acceptable Use Policy forbids cold sends from
owned domains; Instantly's rotating cold-sender pool absorbs the
reputation risk. Per `docs/email-dispatch-architecture.md` §0:

| Tier | Provider | Surface |
|---|---|---|
| Cold (unverified prospects) | **Instantly** | this dispatcher |
| Warm (replied / opted-in) | Resend | `ResendEmailSender` (PR #281) |
| LinkedIn | HeyReach | Phase 17.x |

---

## Environment variables

Add to `.env.example` + per-environment `.env` files. Mirror in
`docs/secret-inventory.md` when wiring lands.

| Var | Required | Purpose |
|---|---|---|
| `INSTANTLY_API_KEY` | yes | Bearer token for `Authorization: Bearer <key>`. Get from Instantly dashboard → Settings → API. |
| `INSTANTLY_DEFAULT_CAMPAIGN_ID` | no | UUID of the default cold campaign. Allows `push_leads()` without explicit `campaign_id=`. Useful for dogfood; explicit per-call is the production pattern. |
| `INSTANTLY_WEBHOOK_SIGNING_SECRET` | no (Phase 14.2) | HMAC-SHA256 secret for webhook signature verification. Set when wiring `POST /webhooks/instantly`. |
| `INSTANTLY_TIMEOUT_SECONDS` | no | HTTP read timeout (default 30). |
| `INSTANTLY_BULK_BATCH_SIZE` | no | Per-batch lead count (default 100; hard cap 1000 per Instantly v2). |

Constructor argument > env var. Both unset → `ValueError` raised at
`push_leads()` (lazy — supports test fixtures that mock-construct
without touching env).

---

## Quick start

```python
import asyncio
from src.integrations.instantly_sender import InstantlyDispatcher
from src.utils.supabase_helper import get_db

async def push_one():
    db = get_db().client
    dispatcher = InstantlyDispatcher(db=db)
    leads = [
        {"email": "ana@example.com",
         "unique_key": "lds-001",
         "first_name": "Ana",
         "company_name": "Acme d.o.o.",
         "lead_source": "google_maps",
         "outreach_score": 73},
    ]
    result = await dispatcher.push_leads(leads)
    print(f"sent={result.success_count} "
          f"suppressed={result.skipped_suppressed} "
          f"failed={result.failed_count}")

asyncio.run(push_one())
```

### Dry-run mode

For Phase 18 review-before-send + sandbox-free E2E tests:

```python
dispatcher = InstantlyDispatcher(db=db, dry_run=True)
result = await dispatcher.push_leads(leads)
assert result.dry_run is True
# - No HTTP call to Instantly
# - No email_send_ledger insert
# - Logger entry: "dry_run: would push N leads to campaign X"
```

`dry_run=True` validates payloads + runs the suppression precheck;
operator sees the resolved batch summary without burning quota OR
leaving a misleading ledger row.

---

## API contract

### `push_leads(leads, campaign_id=None, personalizations=None) -> InstantlyPushResult`

Bulk-pushes 1..N LDS lead rows to an Instantly campaign.

- `leads`: list of dict-shaped Supabase `leads` rows. Required key:
  `email`. Optional: `first_name`, `last_name`, `company_name`,
  `website`, `unique_key`, `outreach_score`, `lead_source`.
- `campaign_id`: UUID of the destination campaign. Falls back to
  `default_campaign_id` constructor arg → `INSTANTLY_DEFAULT_CAMPAIGN_ID`
  env. Both unset → `ValueError`.
- `personalizations`: optional `{unique_key: opener_text}` dict. The
  AI-personalization layer (Phase 15) writes its output here; without
  it, Instantly falls back to the campaign-level template.

Returns `InstantlyPushResult`:

```python
class InstantlyPushResult(BaseModel):
    success_count: int          # leads Instantly accepted
    skipped_suppressed: int     # in suppressions table at push time
    failed_count: int           # Instantly rejected (auth, rate, validation)
    errors: list[InstantlyError]
    raw_response: dict          # last-batch API body (debug-only)
    dry_run: bool
```

### `send(...)` — **not supported**

`EmailDispatcher.send()` is per-message; Instantly's model is
campaign-centric (push leads; campaign owns subject/body templates).
Calling `send()` raises `NotImplementedError` — the DispatcherRouter
(Phase 14.4) fails fast on mis-routing.

---

## Schema dependencies

| Table | Column | Source |
|---|---|---|
| `email_send_ledger` | `provider TEXT` CHECK allowlist incl. `'instantly'` | PR #319 |
| `email_send_ledger` | `recipient_domain TEXT NULL` (LinkedIn relaxation) | PR #319 |
| `email_suppression` | `email TEXT PRIMARY KEY` + `reason CHECK` | PR #286 (renamed in Phase 14.2 — see `suppressions` row below) |
| `email_suppression` | `source TEXT NULL` CHECK allowlist incl. `'instantly'` | PR #319 (renamed to `source_provider` in Phase 14.2) |
| `suppressions` | RENAME from `email_suppression`; generic `(identifier_type, identifier_value, channel)` shape; `source_provider` replaces `source`; reason allowlist extended for webhook taxonomy + RFC 8058 + GDPR | PR α (Phase 14.2) |

Suppression precheck is **fail-OPEN**: a transient PostgREST blip
returns an empty suppression set rather than blocking the dispatch.
Rationale: one extra send to a should-have-been-suppressed address
trips Instantly's bounce → next webhook → re-suppresses. Worse outcome
is dispatch stuck on infrastructure flakes.

---

## Risks + known-issues

1. **API v2 only.** v1 (`/api/v1/lead/add`) is decom'd as of 2025-Q4.
   Hard-coded `INSTANTLY_BASE_URL = "https://api.instantly.ai/api/v2"`.
2. **Hard cap 1000/batch.** We batch at 100 by default for safer error
   recovery (1000-row failure loses 10× the work).
3. **Suppression precheck cost.** One DB SELECT per `push_leads()`
   call (batch query, not per-lead). Cost is amortized across the
   whole batch.
4. **No `Idempotency-Key` plumbing yet.** Instantly dedupes by `email`
   per campaign — duplicate sends to the same recipient in the same
   campaign are accepted but no-op'd on their side. Phase 14.3 will
   wire idempotency keys if we see retry-amplified duplicates in the
   wild.
5. **Fail-OPEN on suppression check.** See above. Document trade-off
   for the operator runbook reviewer.
6. **`send()` raises.** DispatcherRouter (Phase 14.4) is the consumer
   that knows to call `push_leads()` instead. Single-shot per-message
   sends MUST route through Resend warm path (PR #281).

---

## Test surface

### Offline (default CI)
- `tests/test_instantly_sender.py` — 19 tests across:
  - Pydantic mapping (`from_lds_lead`, `extra='forbid'`, LDS_KEYS pin)
  - Dispatcher construction (AUP invariant, batch size bounds)
  - Error paths (no campaign, no API key, empty leads)
  - Suppression precheck (batch query, dry-run pass-through)
  - Dry-run behaviour (no API touch, no ledger insert)

### Live (`@pytest.mark.live`, opt-in)
- Single-lead push to sandbox campaign — requires
  `INSTANTLY_API_KEY` + `INSTANTLY_DEFAULT_CAMPAIGN_ID` env.

```bash
# Offline only
pytest tests/test_instantly_sender.py -q

# Including the live tier
INSTANTLY_API_KEY=...  INSTANTLY_DEFAULT_CAMPAIGN_ID=...  \
  pytest tests/test_instantly_sender.py -m "live"
```

---

## Operator checklist (Phase 14 dogfood ready)

- [ ] Instantly account provisioned, billing live
- [ ] Sandbox campaign created (used for dry-run validation)
- [ ] `INSTANTLY_API_KEY` set in backend env (Render + local `.env`)
- [ ] `INSTANTLY_DEFAULT_CAMPAIGN_ID` set (optional but recommended)
- [ ] Webhook endpoint configured in Instantly dashboard → `Phase 14.2`
- [ ] First cold campaign created with at least one warm-up template
- [ ] DispatcherRouter wired (Phase 14.4) — until then, this
      dispatcher is `push_leads()`-callable but NOT auto-invoked

---

## See also

- [`email-dispatch-architecture.md`](../email-dispatch-architecture.md)
  §0 — multi-dispatcher pivot
- [`email-deliverability.md`](../email-deliverability.md) — DNS / DKIM
  /SPF / DMARC (applies to warm Resend; cold via Instantly inherits
  Instantly's pool reputation, not ours)
- PR #281 — `EmailDispatcher` Protocol + `ResendEmailSender`
- PR #319 — `email_send_ledger.provider` + `email_suppression.source`
