-- ============================================================================
-- Migration: 2026-05-27 security hardening CHECKs
-- ============================================================================
-- Adds 6 CHECK constraints + 1 column from the multi-pass security audit
-- (commit 5eefea2 — Phase 14/15 hardening). All blocks wrapped in idempotent
-- DO $$ BEGIN ... EXCEPTION WHEN duplicate_object so re-runs are safe.
--
-- Pre-flight (none of these should violate the new constraints on existing
-- rows, but a one-liner to confirm before apply):
--
--   SELECT
--     (SELECT COUNT(*) FROM public.campaign_messages
--        WHERE bounce_reason IS NOT NULL AND length(bounce_reason) > 200)
--      AS bounce_reason_oversize,
--     (SELECT COUNT(*) FROM public.webhook_events
--        WHERE length(event_id) < 1 OR length(event_id) > 256)
--      AS event_id_oversize,
--     (SELECT COUNT(*) FROM public.sequence_steps
--        WHERE send_window_start >= send_window_end)
--      AS window_ordering_violations,
--     (SELECT COUNT(*) FROM public.sequence_steps
--        WHERE send_days !~ '^(mon|tue|wed|thu|fri|sat|sun)(,(mon|tue|wed|thu|fri|sat|sun))*$')
--      AS send_days_violations,
--     (SELECT COUNT(*) FROM public.sequence_variants
--        WHERE length(body_template) > 16384
--           OR (subject_template IS NOT NULL AND length(subject_template) > 998))
--      AS body_size_violations;
--
-- All should be 0. If not, fix the offending rows before applying this block.
-- ============================================================================

-- 1. campaign_messages.bounce_reason ≤ 200
DO $$ BEGIN
    ALTER TABLE public.campaign_messages
        ADD CONSTRAINT campaign_messages_bounce_reason_size
        CHECK (bounce_reason IS NULL OR length(bounce_reason) <= 200);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- 2. webhook_events.event_id ∈ [1, 256]
DO $$ BEGIN
    ALTER TABLE public.webhook_events
        ADD CONSTRAINT webhook_events_event_id_size
        CHECK (length(event_id) BETWEEN 1 AND 256);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- 3. sequence_steps.send_window_start < send_window_end
DO $$ BEGIN
    ALTER TABLE public.sequence_steps
        ADD CONSTRAINT sequence_steps_window_ordered
        CHECK (send_window_start < send_window_end);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- 4. sequence_steps.send_days regex allowlist
DO $$ BEGIN
    ALTER TABLE public.sequence_steps
        ADD CONSTRAINT sequence_steps_send_days_format
        CHECK (send_days ~ '^(mon|tue|wed|thu|fri|sat|sun)(,(mon|tue|wed|thu|fri|sat|sun))*$');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- 5. sequence_variants.content_type column (idempotent backfill)
ALTER TABLE public.sequence_variants
    ADD COLUMN IF NOT EXISTS content_type TEXT NOT NULL DEFAULT 'text';

-- 6. sequence_variants.body_template ≤ 16384, subject_template ≤ 998
DO $$ BEGIN
    ALTER TABLE public.sequence_variants
        ADD CONSTRAINT sequence_variants_body_size
        CHECK (length(body_template) <= 16384
            AND (subject_template IS NULL OR length(subject_template) <= 998));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- 7. sequence_variants.content_type ∈ ('text','html')
DO $$ BEGIN
    ALTER TABLE public.sequence_variants
        ADD CONSTRAINT sequence_variants_content_type_allowed
        CHECK (content_type IN ('text', 'html'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ============================================================================
-- Post-apply verification
-- ============================================================================
--   SELECT conname FROM pg_constraint WHERE conname IN (
--     'campaign_messages_bounce_reason_size',
--     'webhook_events_event_id_size',
--     'sequence_steps_window_ordered',
--     'sequence_steps_send_days_format',
--     'sequence_variants_body_size',
--     'sequence_variants_content_type_allowed'
--   );
--   -- Should return 6 rows.
--
--   SELECT column_name, data_type, column_default, is_nullable
--   FROM information_schema.columns
--   WHERE table_schema = 'public'
--     AND table_name = 'sequence_variants'
--     AND column_name = 'content_type';
--   -- Should return 1 row: content_type | text | 'text'::text | NO
-- ============================================================================
