# Sequencing engine (Phase 15)

Phase 15 turns the Phase 14 single-shot dispatcher into a multi-step
outreach engine with A/B variant testing, send-window enforcement,
and webhook-driven sequence advancement. This document captures the
architecture, the four PRs that ship it, the race conditions and
their mitigations, and the operator surface.

## Status

| PR | Scope |
|---|---|
| **15.1 (this PR)** | Schema (sequences / sequence_steps / sequence_variants) + repository layer + drift gate updates |
| 15.2 | Dispatch tick worker (Render Cron entry point) + send-window resolver |
| 15.3 | Variant selector + Jinja2 template renderer + thread-continuation builder |
| 15.4 | Webhook-driven sequence advancement (cancel-pending on bounce/unsub/reply, advance-to-next-step on sent) |

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
