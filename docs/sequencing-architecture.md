# Sequencing engine (Phase 15)

Phase 15 turns the Phase 14 single-shot dispatcher into a multi-step
outreach engine with A/B variant testing, send-window enforcement,
and webhook-driven sequence advancement. This document captures the
architecture, the four PRs that ship it, the race conditions and
their mitigations, and the operator surface.

## Status

| PR | Scope |
|---|---|
| 15.1 | Schema (sequences / sequence_steps / sequence_variants) + repository layer + drift gate updates |
| 15.2 | Dispatch tick worker (Render Cron entry point) + send-window resolver + atomic claim |
| **15.3 (this PR)** | Variant selector + Jinja2 SandboxedEnvironment template renderer + thread-continuation builder + batch-fetch repos (N+1 elimination) + dispatch_tick rewire |
| 15.4 | Webhook-driven sequence advancement (cancel-pending on bounce/unsub/reply, advance-to-next-step on sent) |

## Phase 15.3 — services layer

Three new services + one orchestration layer between handlers and repos:

- **`src/services/template_renderer.py`** — Jinja2 `SandboxedEnvironment`
  with `StrictUndefined` + `select_autoescape` (HTML mode only).
  `ALLOWED_VARS` allowlist enforced at variant-CREATE time via
  `validate_template_vars()` (walks the Jinja2 AST — trim modifiers /
  filters don't fool the check). `assert_cold_email_unsubscribe()`
  rejects email variants that don't reference `{{ unsubscribe_url }}`
  (RFC 8058 + Instantly AUP). `render()` filters the binding context
  to ALLOWED_VARS before binding — extra keys silently dropped so an
  unbounded lead row can't smuggle data into the template.

- **`src/services/variant_selector.py`** — weighted-random selection
  by `SequenceVariant.weight`. `deterministic_seed` kwarg honored ONLY
  when env `VARIANT_SELECTOR_ALLOW_SEED=1` (literal "1", no whitespace
  tolerance). Production env never sets the gate; pytest fixtures do.
  Logs warning + falls through to `SystemRandom` when seed slips
  through without the gate.

- **`src/services/thread_builder.py`** — assembles `DispatchPayload`
  (lds_message_id, lead_unique_key, email, subject, body,
  in_reply_to_message_id, list_unsubscribe_url) from lead + step +
  variant + optional prior_message. `step.thread_with_prior=True`
  + missing prior's `provider_message_id` → raises
  `PriorMessageNotReadyError`. Worker catches and reschedules
  `scheduled_at += 1h` (race vs prior step's webhook delivery).

- **`src/services/variant_service.py`** — orchestrates
  `validate_template_vars` → `assert_cold_email_unsubscribe`
  (channel-conditional) → `SequenceVariantRepository.create`.
  Returns structured `CreateVariantResult(ok, variant, error_code,
  error_message, disallowed_vars)`. Used by Phase 18 UI + future AI
  generator.

## Phase 15.3 — batch-fetch + N+1 prevention

The dispatch tick (Phase 15.2) used placeholder
`recipient_email` fields on the `campaign_messages` row. 15.3
replaces with a 4-PostgREST-call batch-fetch pattern across the
claimed batch:

| Repo method | Returns | Backed by |
|---|---|---|
| `LeadRepository.fetch_many(unique_keys)` | `{unique_key → row}` | `WHERE unique_key IN (...)` |
| `SequenceStepRepository.fetch_many(step_ids)` | `{step_id → SequenceStep}` | `WHERE id IN (...)` |
| `SequenceVariantRepository.fetch_many_for_steps(step_ids)` | `{step_id → list[SequenceVariant]}` | `WHERE step_id IN (...)` ordered by `variant_label` |
| `CampaignMessageRepository.fetch_many(message_ids)` | `{id → row}` | `WHERE id IN (...)` |

Total SELECTs per tick: 4 (not 4×N). Pinned by
`tests/unit/test_batch_fetch_n1.py::TestN1Prevention`.

## Phase 15.3 — dispatch_tick rewire

Per-message build loop (replaces the 15.2 placeholder filter):

```
for row in claimed:
    1. resolve lead via leads_by_uk[row.lead_unique_key]
       - missing → release as 'failed' (no_email_or_lead_row)
    2. suppression check
       - hit → release as 'cancelled' (channel='all' suppression)
    3. resolve step via steps_by_id[row.step_id]
       - missing → release as 'failed' (missing_step)
    4. send-window check using step's own send_window_*
       - out → release as 'pending', scheduled_at = next_window_start_utc
    5. variant select via select_variant(variants_by_step[step_id])
       - empty → release as 'failed' (no_variants)
    6. build_send_payload(lead, step, variant, prior_message=...)
       - PriorMessageNotReadyError → release as 'pending', scheduled_at += 1h
       - TemplateError → release as 'failed' with error reason
    7. append payload.as_lead_dict() to leads_payload + register message_id
```

Survivors go to `dispatcher.push_leads(leads, message_ids)`.

## Configuration

| Env var | Purpose |
|---|---|
| `OPERATOR_NAME` | Injected as `{{ operator_name }}` in every render |
| `OPERATOR_SIGNATURE` | Injected as `{{ operator_signature }}` |
| `UNSUBSCRIBE_BASE_URL` | Base for `{{ unsubscribe_url }}` = `<base>/u/<tracking_id>` |
| `VARIANT_SELECTOR_ALLOW_SEED` | TEST ONLY — literal `1` enables deterministic seed path. NEVER set in production. |

## Schema diagram

```
┌─────────────────────┐
│ campaigns           │
│ id, name, status,   │
│ channel, ...        │
└──────────┬──────────┘
           │ 1:N
           ▼
┌─────────────────────┐
│ sequences           │
│ id, campaign_id,    │
│ name, status        │   draft → active → paused → archived
└──────────┬──────────┘
           │ 1:N (ordered by step_index)
           ▼
┌─────────────────────┐
│ sequence_steps      │
│ id, sequence_id,    │
│ step_index, channel,│
│ delay_days/hours,   │
│ thread_with_prior,  │
│ branch_condition,   │
│ send_window_*,      │
│ send_days           │
└──────────┬──────────┘
           │ 1:N
           ▼
┌─────────────────────┐
│ sequence_variants   │
│ id, step_id,        │
│ variant_label A-Z,  │
│ subject_template,   │
│ body_template,      │
│ weight, ai_*        │
└─────────────────────┘
           
campaign_messages (extended)
  + step_id      → sequence_steps.id
  + variant_id   → sequence_variants.id
  + scheduled_at TIMESTAMPTZ   (dispatch_tick reads this)
  + dispatched_at TIMESTAMPTZ  (set when dispatch_tick claims the row)
```

## State machine (per row in `campaign_messages`)

```
                       ┌─ webhook email_sent ──► sent
                       │                          │
                       │                          ├─ email_bounced ─► bounced (terminal)
pending ──► dispatching│                          ├─ email_unsubscribed ─► unsubscribed (terminal)
   ▲          │        │                          └─ email_replied ─► replied
   │          │        │
   │          └─ send_failed ──► bounced (terminal — dispatcher-side)
   │
   ◄─── advance-from-prior-step (Phase 15.4)
```

`pending → dispatching` lands in Phase 15.2 (status allowlist
extension + atomic claim pattern). `pending → cancelled` lands in
Phase 15.4 (cancel-pending-on-terminal-event).

## Repository layer (Phase 15.1)

All three new repos follow the established pattern (PostgREST chain
API; no raw SQL; idempotent UPDATE predicates):

### `SequenceRepository` (`src/repositories/sequence_repo.py`)

- `list_active_for_campaign(campaign_id) → list[Sequence]`
  Backed by partial index `idx_sequences_campaign_active`.
- `get_by_id(sequence_id) → Sequence | None`
- `create(campaign_id, name, status='draft') → Sequence | None`
- `update_status(sequence_id, new_status) → bool`
  Idempotent — `.neq("status", new_status)` predicate so re-application
  doesn't bump `updated_at`.

### `SequenceStepRepository` (`src/repositories/sequence_step_repo.py`)

- `list_for_sequence(sequence_id) → list[SequenceStep]`
  Ordered by `step_index` ascending; backed by
  `idx_sequence_steps_lookup`.
- `get_by_index(sequence_id, step_index) → SequenceStep | None`
  Used by Phase 15.4 sequence advancer to find the next step.
- `create(...) → SequenceStep | None`
  Returns None on UNIQUE collision (same `(sequence_id, step_index)`).

### `SequenceVariantRepository` (`src/repositories/sequence_variant_repo.py`)

- `list_for_step(step_id) → list[SequenceVariant]`
  Ordered by `variant_label` so the selector sees stable A,B,C order
  — important for deterministic-seed tests.
- `create(step_id, variant_label, body_template, ...) → SequenceVariant | None`
  Client-side label format + positive-weight validation before round
  trip. DB CHECK constraints are the authoritative gate; pre-check
  keeps the error path uniform with `step.create()`.

### `CampaignMessageRepository` extensions

- `fetch_due_for_dispatch(limit=100, now_iso=None) → list[dict]`
  PostgREST: `WHERE status='pending' AND scheduled_at <= now() ORDER BY
  scheduled_at LIMIT N`. Backed by the partial index
  `idx_campaign_messages_dispatch_queue`. Returns raw row dicts (not a
  typed dataclass) — Phase 15.2's dispatch_tick joins lead + step +
  variant data per row.
- `schedule_step(message_id, step_id, variant_id, scheduled_at_iso) → MarkResult`
  Idempotent UPDATE gated by `status='pending'` — terminal-state rows
  excluded so a late re-schedule on a sent / bounced row is a no-op
  rather than silent history rewrite.

## Research-pinned design decisions

These are inputs to the schema choices; reviewers don't have to
re-derive them when touching the tables.

- **4-5 steps over 14-21 days** = empirical sweet spot for cold
  outreach reply rates (research aggregate 2026-05-25).
- **Day 1 / 3 / 7 / 14 / 21 cadence** (widening gaps, not uniform) —
  modeled via `sequence_steps.delay_days` per step.
- **Steps 1-3 share a thread** (blank subject → mail client renders
  `Re: <prior>`); **step 4+ optionally breaks** thread to surface a
  new subject. Modeled via `sequence_steps.thread_with_prior` boolean.
- **Most replies arrive in steps 2-4**, not step 1 — single-shot
  campaigns leave most reply intent on the table.
- **A/B variants at step level**, not campaign level — operators
  iterate on copy mid-campaign without re-creating the whole sequence.
- **`variant_label` constrained to single uppercase letter [A-Z]** =
  26 max per step. Researched cap is 3-5 in practice; the schema
  constraint is generous, consistent, and drives uniform UI / log /
  analytics labels.

## Race conditions (mitigations land in 15.2 / 15.4)

This PR (15.1) is read-mostly schema + repos; the dispatcher race
mitigations land alongside their code:

### Two `dispatch_tick` runs claiming the same row (Phase 15.2)

Mitigation: `pending → dispatching` atomic transition. After the
SELECT, a second-phase UPDATE with `WHERE id IN (...) AND
status='pending'` ensures only one worker successfully claims each
row. Wasted SELECT work but correct semantics.

### Webhook arrives before dispatch_tick processes the due row (Phase 15.4)

Mitigation: `mark_sent` already uses `.is_("provider_message_id",
"null")` first-hit-wins predicate (Phase 14.3). If the webhook fires
first, `provider_message_id` is stamped + status='sent'; the
subsequent dispatch_tick sees `status != 'pending'` and skips the row.

### Sequence advancement race: `_replied` and `_sent` for same row (Phase 15.4)

Mitigation: advancement is keyed on the message's terminal status
post-transition. `mark_replied` only fires from `status='sent'`; once
either event lands, the predicate excludes the other branch. The
advancer reads the resulting status from a single SELECT
post-transition.

## Out of scope (Phase 16 / 17 / 18 / 19)

- AI reply classifier — labels pos / neg / OOO / objection (Phase 16)
- HeyReach LinkedIn dispatcher (Phase 17)
- AI personalization research → write → judge loop (Phase 18)
- Sequence builder UI (Phase 18)
- Lead-level timezone column + geocoding resolution (Phase 19)
- Saturday opt-in send_days (Phase 19)

## Operator follow-up after merge

1. Apply schema migration to live Supabase (`ALTER` chain idempotent;
   3 new tables + 4 new `campaign_messages` columns + partial index).
2. No new env vars in 15.1 — `DISPATCH_TICK_BATCH_SIZE`,
   `SEND_WINDOW_DEFAULT_TZ`, etc. land alongside Phase 15.2's worker.
3. No UI change — sequence creation is API-only until Phase 18.
