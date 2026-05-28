-- ============================================================================
-- Migration: 2026-05-27 apostrophe-fix + leads.last_name column
-- ============================================================================
-- Fixes 3 prod drift bugs uncovered during Day 1 dogfood smoke (2026-05-27):
--
--   1. sequence_steps_send_days_format had literal ' chars baked INSIDE the
--      regex pattern. The source migration (2026-05-27_security-hardening-
--      checks.sql) used single-quote string syntax `'^...$'`, but the apply
--      path (psql -c shell escape or similar single-quote wrapping) doubled
--      the apostrophes to `''^...$''`, which Postgres parsed as ONE literal
--      apostrophe per pair. End result: regex pattern `'^...$'` (with literal
--      leading + trailing apostrophes). No real send_days value starts with
--      `'` — CHECK rejected every insert, including the column default.
--
--   2. sequence_variants_content_type_allowed had the same double-apostrophe
--      problem applied to the IN-list literals — stored as `'''text'''` and
--      `'''html'''`, which Postgres parsed as 6-char strings `'text'` and
--      `'html'` (the apostrophes are part of the value). Every INSERT with
--      content_type='text' was rejected.
--
--   3. leads.last_name column referenced by 6 call sites in src/ + backend/
--      (lead_repo._LEAD_FIELDS, instantly_models.InstantlyLeadPayload,
--      thread_builder, template_renderer, etc.) but missing from prod.
--      Caused dispatch_tick → lead_repo.fetch_many to fail with PostgREST
--      42703 → every dispatch marked failed with bounce_reason=
--      'no_email_or_lead_row'.
--
-- Apply-path robustness:
--   * E'' prefix on the regex literal in (1) so the parser knows it's a raw
--     string and any future re-wrapping doesn't get misinterpreted.
--   * IN ('text','html') with no shell-wrapping for (2).
--   * ADD COLUMN IF NOT EXISTS for (3) — idempotent regardless of repeat.
--
-- Bug memory: bug_constraint_apostrophe_double_escape_2026-05-27.md
-- Audit precedent: this migration file IS the audit trail per user
-- direction 2026-05-27 ("audit-trail PR for the apostrophe fix").
--
-- Pre-flight (run before apply; all should be 0):
--   SELECT
--     (SELECT count(*) FROM public.sequence_steps
--        WHERE send_days !~ E'^(mon|tue|wed|thu|fri|sat|sun)(,(mon|tue|wed|thu|fri|sat|sun))*$')
--      AS sequence_steps_send_days_violations,
--     (SELECT count(*) FROM public.sequence_variants
--        WHERE content_type NOT IN ('text','html'))
--      AS sequence_variants_content_type_violations;
-- ============================================================================

-- (1) sequence_steps_send_days_format — drop broken, re-add with E''-prefix
DO $$ BEGIN
    ALTER TABLE public.sequence_steps
        DROP CONSTRAINT IF EXISTS sequence_steps_send_days_format;
    ALTER TABLE public.sequence_steps
        ADD CONSTRAINT sequence_steps_send_days_format
        CHECK (send_days ~ E'^(mon|tue|wed|thu|fri|sat|sun)(,(mon|tue|wed|thu|fri|sat|sun))*$');
END $$;

-- (2) sequence_variants_content_type_allowed — drop broken, re-add clean IN
DO $$ BEGIN
    ALTER TABLE public.sequence_variants
        DROP CONSTRAINT IF EXISTS sequence_variants_content_type_allowed;
    ALTER TABLE public.sequence_variants
        ADD CONSTRAINT sequence_variants_content_type_allowed
        CHECK (content_type IN ('text', 'html'));
END $$;

-- (3) leads.last_name column — add if missing (idempotent)
ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS last_name TEXT;

-- Post-apply verification (manual; expected outputs in comments):
--   SELECT pg_get_constraintdef(oid)
--     FROM pg_constraint WHERE conname='sequence_steps_send_days_format';
--   -- expect: CHECK ((send_days ~ '^(mon|tue|wed|thu|fri|sat|sun)(,(mon|tue|wed|thu|fri|sat|sun))*$'::text))
--
--   SELECT pg_get_constraintdef(oid)
--     FROM pg_constraint WHERE conname='sequence_variants_content_type_allowed';
--   -- expect: CHECK ((content_type = ANY (ARRAY['text'::text, 'html'::text])))
--
--   SELECT column_name, data_type, is_nullable
--     FROM information_schema.columns
--    WHERE table_schema='public' AND table_name='leads' AND column_name='last_name';
--   -- expect: last_name | text | YES
