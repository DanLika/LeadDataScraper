-- ============================================================================
-- Migration: 2026-05-30 Phase 16 — reply_classifications + auto-pause state
-- ============================================================================
-- Three additions matching supabase_schema.sql Phase 16 section:
--
--   1. CREATE TABLE public.reply_classifications + 3 CHECK + 1 UNIQUE
--      + 2 indexes + RLS deny-all (REVOKE + RESTRICTIVE policy).
--   2. ALTER public.sequences ADD COLUMN paused_on_reply BOOLEAN
--      + ADD COLUMN pause_reason TEXT + CHECK ≤200 + partial index.
--   3. DROP+ADD campaign_messages.status CHECK with 'paused_by_reply'
--      appended to the existing allowlist (same constraint name so
--      schema_drift_check.py EXPECTED_CHECK_CONSTRAINTS does not change).
--
-- All blocks are idempotent (CREATE IF NOT EXISTS / DO $$ EXCEPTION
-- WHEN duplicate_object / DROP IF EXISTS) so re-runs are safe.
--
-- Pre-flight (zero existing-row violations expected — fresh schema, no
-- rows reference these columns yet — but cheap to verify):
--
--   SELECT
--     (SELECT COUNT(*) FROM public.campaign_messages
--        WHERE status IS NOT NULL
--          AND status NOT IN ('pending','dispatching','sent','delivered',
--                             'replied','bounced','unsubscribed',
--                             'cancelled','failed','paused_by_reply'))
--      AS status_allowlist_violations,
--     (SELECT COUNT(*) FROM public.sequences)
--      AS sequences_total_will_default_to_false,
--     (SELECT to_regclass('public.reply_classifications') IS NOT NULL)
--      AS reply_classifications_already_exists;
--
-- Apply via Supabase Management API (single POST to
-- /v1/projects/{ref}/database/query). Token: SUPABASE_PERSONAL_ACCESS_TOKEN
-- env. Operator action — bot does not have a fresh PAT 2026-05-30 (rotation
-- deferred since 2026-05-27 leak).
--
-- Post-apply verification: run scripts/migrations/_verify_constraints.py
-- + python -m src.scripts.schema_drift_check  (both should exit 0).
-- ============================================================================

-- 1. reply_classifications table -------------------------------------------

CREATE TABLE IF NOT EXISTS public.reply_classifications (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_unique_key     TEXT NOT NULL REFERENCES public.leads(unique_key) ON DELETE CASCADE,
    campaign_message_id UUID REFERENCES public.campaign_messages(id) ON DELETE SET NULL,
    message_body_hash   TEXT NOT NULL,
    classification      TEXT NOT NULL,
    confidence          DOUBLE PRECISION NOT NULL,
    reasoning           TEXT,
    model_version       TEXT NOT NULL,
    classified_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    ALTER TABLE public.reply_classifications
        ADD CONSTRAINT reply_classifications_classification_allowed
        CHECK (classification IN (
            'interested', 'not_interested', 'ooo', 'wrong_person',
            'asking_for_info', 'unsubscribe_request', 'complaint',
            'bounce_soft', 'bounce_hard', 'auto_reply', 'other'
        ));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.reply_classifications
        ADD CONSTRAINT reply_classifications_confidence_range
        CHECK (confidence >= 0 AND confidence <= 1);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.reply_classifications
        ADD CONSTRAINT reply_classifications_body_hash_format
        CHECK (length(message_body_hash) BETWEEN 16 AND 128);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.reply_classifications
        ADD CONSTRAINT reply_classifications_unique_classification
        UNIQUE (lead_unique_key, message_body_hash);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_reply_classifications_lead_recent
    ON public.reply_classifications (lead_unique_key, classified_at DESC);

CREATE INDEX IF NOT EXISTS idx_reply_classifications_classification
    ON public.reply_classifications (classification);

ALTER TABLE public.reply_classifications ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.reply_classifications FROM anon, authenticated, PUBLIC;

DO $$ BEGIN
    CREATE POLICY reply_classifications_deny_all ON public.reply_classifications
        AS RESTRICTIVE
        FOR ALL
        TO anon, authenticated
        USING (false)
        WITH CHECK (false);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- 2. sequences: paused_on_reply + pause_reason -----------------------------

ALTER TABLE public.sequences
    ADD COLUMN IF NOT EXISTS paused_on_reply BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE public.sequences
    ADD COLUMN IF NOT EXISTS pause_reason TEXT;

DO $$ BEGIN
    ALTER TABLE public.sequences
        ADD CONSTRAINT sequences_pause_reason_size
        CHECK (pause_reason IS NULL OR length(pause_reason) <= 200);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_sequences_paused_on_reply
    ON public.sequences (paused_on_reply)
    WHERE paused_on_reply = true;

-- 3. campaign_messages.status += 'paused_by_reply' -------------------------
-- DROP+ADD with the SAME constraint name preserves the drift-gate count
-- (EXPECTED_CHECK_CONSTRAINTS["campaign_messages"] still sees one entry
-- named campaign_messages_status_allowed).

DO $$ BEGIN
    ALTER TABLE public.campaign_messages
        DROP CONSTRAINT IF EXISTS campaign_messages_status_allowed;
EXCEPTION WHEN undefined_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.campaign_messages
        ADD CONSTRAINT campaign_messages_status_allowed
        CHECK (status IS NULL OR status IN (
            'pending', 'dispatching', 'sent', 'delivered',
            'replied', 'bounced', 'unsubscribed', 'cancelled', 'failed',
            'paused_by_reply'
        ));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
