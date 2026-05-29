# CHECK constraint apostrophe double-escape drift

**Status**: RESOLVED in prod 2026-05-27. Recurrence-guarded by `E''`-prefix
convention + post-apply `pg_get_constraintdef` audit step.

## Symptom

Phase 14/15 dispatch pipeline can't insert a single `sequence_steps` row.
PostgREST error surfaced via dispatcher logs:

```
new row for relation "sequence_steps" violates check constraint
  "sequence_steps_send_days_format"
DETAIL: Failing row contains (..., 'mon,tue,wed,thu,fri,sat,sun', ...)
```

Same error on EVERY tested value: `'mon'`, `'monday'`, `'1,2,3,4,5'`,
`''`, `'mon,tue,wed,thu,fri'` (the column default), `'mon,tue,wed,thu,fri,sat,sun'`.
Failing row message shows the value cleanly — no input-side encoding issue.

Earlier "4/4 prod webhook E2E green" entry was misleading: those were synthetic
webhook POSTs, not real dispatch. Real dispatch had never inserted a row.

## Root cause

When `supabase_schema.sql` was applied to prod via the Supabase Management
API on 2026-05-27, JSON-encoding the SQL payload doubled apostrophes inside
CHECK literals. Source intent:

```sql
CHECK (send_days ~ '^(mon|tue|wed|thu|fri|sat|sun)(,(mon|tue|wed|thu|fri|sat|sun))*$')
```

Prod stored constraint (per `pg_get_constraintdef`):

```sql
CHECK (send_days ~ '''^(mon|...)*$''')
```

Postgres parsed `''` as a single literal apostrophe, so the regex pattern
became `'^(mon|...)*$'` — a 7-char pattern bracketed by literal `'` characters.
Real values like `mon,tue,wed,thu,fri` don't start with `'` → CHECK rejects
every insert including the column default.

Same defect class hit `sequence_variants_content_type_allowed` (IN-list
literals — `'text'`/`'html'` stored as 6-char strings with literal `'` chars).

**Spec-side trap**: Management API returns `200 OK` regardless of whether the
constraint body parses to the intended semantics. No automatic post-apply
verification.

## Fix recipe (idempotent, prod-safe)

Run from Supabase Studio SQL Editor (MCP under `ababic785@gmail.com` can't
reach `kbtkxpvchmunwjykbeht` which lives under `duskolicanin1234@gmail.com`):

```sql
-- 1. Diagnose: triple-apostrophe smell
SELECT conname, pg_get_constraintdef(oid) AS def
FROM pg_constraint
WHERE conname LIKE '%_format' OR conname LIKE '%_allowed';
-- Look for '''pattern''' (triple apostrophes) in def.

-- 2. Fix with E''-prefix
DO $$ BEGIN
    ALTER TABLE public.sequence_steps
      DROP CONSTRAINT IF EXISTS sequence_steps_send_days_format;
    ALTER TABLE public.sequence_steps
      ADD CONSTRAINT sequence_steps_send_days_format
      CHECK (send_days ~ E'^(mon|tue|wed|thu|fri|sat|sun)(,(mon|tue|wed|thu|fri|sat|sun))*$');
END $$;

-- 3. Sanity probe (should return true)
SELECT 'mon,tue,wed,thu,fri,sat,sun' ~
  E'^(mon|tue|wed|thu|fri|sat|sun)(,(mon|tue|wed|thu|fri|sat|sun))*$' AS should_be_true;
```

`E''`-prefix sidesteps future re-escaping rounds. Sent via Management API as
raw JSON: JSON doesn't escape apostrophes, so `E''`-prefixed strings pass
through clean.

**Side fix in same migration** (PR #366): `leads.last_name` column was
missing in prod — code references it via 6 call sites
(`lead_repo._LEAD_FIELDS`, `instantly_models`, `thread_builder`,
`template_renderer`, `backend/main`, `export_leads`). Caused
`dispatch_tick → lead_repo.fetch_many` to fail with PostgREST 42703,
marking every message `failed` with `bounce_reason='no_email_or_lead_row'`.
PR #366 adds the column + updates both `CREATE TABLE` and `ALTER` blocks.

## Recurrence guard

1. **Pre-apply** — for any future migration with regex CHECKs:
   - Always use `E''`-prefix on the literal.
   - Test every CHECK with at least one positive case AND the column default
     before declaring "applied".
2. **Post-apply** — mandatory verification step:
   ```sql
   SELECT conname, pg_get_constraintdef(oid)
   FROM pg_constraint
   WHERE conname = '<your_constraint>';
   ```
   Confirm stored pattern matches source. `200 OK` from Management API ≠
   "works as intended".
3. **CI gate (PR #372)** — `python -m scripts.migrations._verify_constraints`
   runs both text-match AND positive INSERT probe per CHECK. Wired into
   `schema-drift-check.yml`.
4. **Dict pairing rule** (PR #377/#380) — same PR must update
   `EXPECTED_CHECK_CONSTRAINTS` in `src/scripts/schema_drift_check.py`.
   See [check-constraint-dict-pairing](./README.md#check-constraint-pairing).

## Other constraints to audit if a future apply repeats the bug

ONLY regex-bearing CHECKs are at risk. Non-regex constraints in same Phase 14/15
batch (`sequence_variants_body_size`, `webhook_events_event_id_size`,
`sequence_steps_window_ordered`, `campaign_messages_bounce_reason_size`) are
length / time-compare — apostrophe doubling doesn't apply.

## Related

- Memory: `bug_constraint_apostrophe_double_escape_2026-05-27.md`,
  `bug_send_days_check_drift_2026-05-27.md`, `phase14-15-canonical-table-names.md`
- PR: #366 (`7843e87c` admin-merged 2026-05-27), #372 (2-layer verifier),
  #377/#380 (dict pairing codification)
- Code: `scripts/migrations/2026-05-27_apostrophe-fix-and-leads-last-name.sql`,
  `supabase_schema.sql`, `src/scripts/schema_drift_check.py`
- Related runbook: [check-constraint-dict-pairing](./README.md#check-constraint-pairing)
