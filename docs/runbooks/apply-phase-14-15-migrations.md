# Apply Phase 14+15 Supabase migrations

The Phase 14+15 stack (#281–#330, merged 2026-05-26) added new tables and columns to `supabase_schema.sql` but did **not** apply them to the live Supabase DB. The schema-drift CI gate will stay red until applied.

## What's in the migration

- **Phase 14.0** — `leads.provider`, `leads.suppression_source` columns (additive)
- **Phase 14.1** — Instantly dispatcher integration (no schema change, code-only)
- **Phase 14.2 PR α** — Rename `email_suppression` → `suppressions` + multi-channel cols
- **Phase 14.2 PR β** — `campaign_messages.thread_id`, `tracking_id` columns + RFC 8058 indexes
- **Phase 14.2 PR γ** — New `webhook_events` table (idempotency) + extend `campaign_messages.status` allowlist
- **Phase 14.3** — No schema (round-trip via custom variable echo, code-only)
- **Phase 15.1** — New tables: `sequences`, `sequence_steps`, `sequence_variants`
- **Phase 15.2** — `campaign_messages.scheduled_at` column + partial index `idx_campaign_messages_dispatch_queue`
- **Phase 15.4** — `campaign_messages.sequence_id` denorm column + advance-idempotency `uniq_message_per_lead_sequence_step` partial UNIQUE index

All DDL is idempotent (`IF NOT EXISTS` / `DO $$ … EXCEPTION` blocks). Re-running is safe.

## Apply path A — Supabase Studio SQL Editor (recommended for one-shot)

1. Open `supabase_schema.sql` in this repo.
2. Copy lines **403 to end** (Phase 14.0 marker onwards).
3. Paste into Supabase Studio → SQL Editor → Run.
4. Verify the next section's queries return expected rows.

## Apply path B — Supabase CLI / MCP (requires token)

1. Set `SUPABASE_ACCESS_TOKEN` to a Personal Access Token from
   https://supabase.com/dashboard/account/tokens
2. Re-invoke this session OR run via CLI:
   ```bash
   supabase db push --project-ref kbtkxpvchmunwjykbeht
   ```

## Post-apply verification (run in SQL Editor)

```sql
-- 1. All expected tables exist
SELECT table_name FROM information_schema.tables
WHERE table_schema='public'
  AND table_name IN ('suppressions','webhook_events','sequences',
                     'sequence_steps','sequence_variants',
                     'email_send_ledger','account_deletions')
ORDER BY table_name;
-- expect 7 rows

-- 2. New campaign_messages columns
SELECT column_name FROM information_schema.columns
WHERE table_schema='public' AND table_name='campaign_messages'
  AND column_name IN ('thread_id','tracking_id','scheduled_at','sequence_id')
ORDER BY column_name;
-- expect 4 rows

-- 3. New leads columns
SELECT column_name FROM information_schema.columns
WHERE table_schema='public' AND table_name='leads'
  AND column_name IN ('provider','suppression_source');
-- expect 2 rows

-- 4. RLS deny-all policies on new tables
SELECT tablename, policyname FROM pg_policies
WHERE schemaname='public'
  AND tablename IN ('suppressions','webhook_events','sequences','sequence_steps','sequence_variants')
ORDER BY tablename;
-- expect 5 rows, all named *_deny_all

-- 5. Status allowlists updated
SELECT con.conname, pg_get_constraintdef(con.oid) AS def
FROM pg_constraint con
JOIN pg_class rel ON con.conrelid = rel.oid
JOIN pg_namespace ns ON rel.relnamespace = ns.oid
WHERE ns.nspname='public'
  AND rel.relname='campaign_messages'
  AND con.contype='c';
-- expect campaign_messages_status_allowed to include 'cancelled' + 'failed_render' + 'failed_dispatch'
```

## Post-apply CHECK-constraint verification (MANDATORY for regex / IN-list CHECKs)

Any migration that lands regex or IN-list literal CHECKs MUST be followed by:

```bash
SUPABASE_ACCESS_TOKEN=sbp_... make verify-prod-constraints
```

This catches the **apostrophe-double-escape bug class** (PR #366,
`scripts/migrations/2026-05-27_apostrophe-fix-and-leads-last-name.sql`)
where the apply path silently doubles `'` inside CHECK literals →
stored predicate becomes `'''pattern'''` → rejects every valid INSERT,
including the column default. Discovered post-Phase 14+15 apply when
`sequence_steps_send_days_format` rejected `mon,tue,wed,thu,fri` and
`sequence_variants_content_type_allowed` rejected `'text'`, stranding
the entire dispatcher pipeline.

The verifier (`scripts/migrations/_verify_constraints.py`) has two
layers:

1. **Stored-DEF inspection** — pulls `pg_get_constraintdef(oid)` for
   each tracked CHECK, asserts (a) no triple-apostrophe smell `'''`
   in the DEF and (b) the canonical form matches an expected regex.
   Zero side effects.
2. **INSERT probe** — for each constraint, attempts a positive value
   that MUST pass + a negative that MUST reject, inside a
   `DO $$ ... RAISE EXCEPTION` block so the whole txn rolls back —
   no row ever commits.

First-time setup: run `make verify-prod-constraints-canary` to confirm
the Management API echoes `RAISE EXCEPTION` messages verbatim. If it
doesn't, Layer 2 degrades to SKIP and Layer 1 carries the load
alone — still sufficient for the apostrophe-double-escape bug class.

Adding a new constraint? Append a `Probe` entry to the `PROBES` list
in `_verify_constraints.py` with the positive / negative test values
and the expected DEF regex. Update only when the source migration
also changes — never to silence a finding.

## Down-migration (rollback)

The Phase 14.2 rename `email_suppression → suppressions` is destructive (no easy revert if rows were written under the new name). All other adds are reversible via:

```sql
-- Phase 15.4
DROP INDEX IF EXISTS public.uniq_message_per_lead_sequence_step;
ALTER TABLE public.campaign_messages DROP COLUMN IF EXISTS sequence_id;

-- Phase 15.2
DROP INDEX IF EXISTS public.idx_campaign_messages_dispatch_queue;
ALTER TABLE public.campaign_messages DROP COLUMN IF EXISTS scheduled_at;

-- Phase 15.1
DROP TABLE IF EXISTS public.sequence_variants;
DROP TABLE IF EXISTS public.sequence_steps;
DROP TABLE IF EXISTS public.sequences;

-- Phase 14.2 PR γ
DROP TABLE IF EXISTS public.webhook_events;
-- (campaign_messages.status allowlist revert: ALTER TABLE … DROP CONSTRAINT
-- campaign_messages_status_allowed; ALTER TABLE … ADD CONSTRAINT … (old list))

-- Phase 14.2 PR β
DROP INDEX IF EXISTS public.idx_campaign_messages_thread_id;
ALTER TABLE public.campaign_messages DROP COLUMN IF EXISTS thread_id;
ALTER TABLE public.campaign_messages DROP COLUMN IF EXISTS tracking_id;

-- Phase 14.0
ALTER TABLE public.leads DROP COLUMN IF EXISTS suppression_source;
ALTER TABLE public.leads DROP COLUMN IF EXISTS provider;
```

Do **not** rollback the `email_suppression → suppressions` rename without a manual data-recovery plan first.

## CI gate impact

After apply, the `schema-drift + RLS posture (Supabase)` gate in `security.yml` should turn **green** on its next run. Run manually via:

```bash
gh workflow run security.yml --ref main
```

(once GH Actions billing is restored).
