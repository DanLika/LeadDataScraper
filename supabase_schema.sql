-- SQL Schema for Supabase
-- Run this in your Supabase SQL Editor

CREATE TABLE IF NOT EXISTS leads (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    unique_key TEXT UNIQUE NOT NULL,
    name TEXT,
    website TEXT,
    email TEXT,
    phone TEXT,
    address TEXT,
    rating FLOAT,
    reviews INTEGER,
    lead_source TEXT,
    audit_status TEXT DEFAULT 'Pending', -- Pending, Processing, Completed, Failed
    audit_results JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    last_error TEXT,
    retry_count INTEGER DEFAULT 0,
    last_processed_at TIMESTAMP WITH TIME ZONE,
    enrichment_status TEXT DEFAULT 'PENDING',
    high_risk_flag BOOLEAN DEFAULT FALSE,
    seo_score INTEGER,
    company_size TEXT,
    leadership_team TEXT,
    key_offerings TEXT,
    contact_details TEXT,
    business_details TEXT,
    target_clients TEXT,
    pain_points TEXT,
    facebook TEXT,
    instagram TEXT,
    linkedin TEXT,
    tiktok TEXT,
    pinterest TEXT,
    outreach_score INTEGER,
    segment TEXT,
    linkedin_hook TEXT,
    email_hook TEXT,
    first_name TEXT,
    company_name TEXT,
    priority_link TEXT,
    needs_manual_review BOOLEAN DEFAULT FALSE
);

-- Table for orchestration jobs
CREATE TABLE IF NOT EXISTS orchestration_jobs (
    id UUID PRIMARY KEY,
    status TEXT NOT NULL, -- starting, running, completed, failed, stopped
    total_count INTEGER DEFAULT 0,
    processed_count INTEGER DEFAULT 0,
    current_phase TEXT,
    filters JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Index for faster lookups.
--
-- `leads(unique_key)` is intentionally NOT indexed here — the UNIQUE
-- constraint on the column auto-creates a btree index (`leads_pkey` in
-- the live DB, since unique_key is the de-facto PK). A second named
-- `idx_leads_unique_key` would be redundant disk + write-amp on insert.
CREATE INDEX IF NOT EXISTS idx_leads_audit_status        ON leads(audit_status);
CREATE INDEX IF NOT EXISTS idx_orchestration_jobs_status ON orchestration_jobs(status);
-- Dashboard hot path: `ORDER BY created_at DESC LIMIT 200`. Index is
-- DESC so the planner can satisfy ORDER BY directly and skip the Sort
-- step (verified via EXPLAIN with enable_seqscan=off).
CREATE INDEX IF NOT EXISTS idx_leads_created_at_desc ON leads(created_at DESC);

-- Campaign management tables (Step 4: Outreach)
CREATE TABLE IF NOT EXISTS campaigns (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'draft',  -- draft, active, paused, completed
    channel TEXT NOT NULL,        -- email, linkedin, multi
    segment_filter TEXT,
    total_leads INTEGER DEFAULT 0,
    sent_count INTEGER DEFAULT 0,
    reply_count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc', now()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc', now()) NOT NULL
);

CREATE TABLE IF NOT EXISTS campaign_messages (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    campaign_id UUID REFERENCES campaigns(id) ON DELETE CASCADE,
    -- ON DELETE SET NULL: a lead can be wiped (operator data-export-delete
    -- / right-to-erasure) but the campaign_message row survives as audit
    -- history. Explicit beats the prior implicit NO ACTION, which would
    -- FK-violate mid-deletion.
    lead_unique_key TEXT REFERENCES leads(unique_key) ON DELETE SET NULL,
    channel TEXT NOT NULL,
    subject TEXT,
    body TEXT,
    status TEXT DEFAULT 'pending', -- pending, sent, delivered, replied, bounced
    sent_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc', now()) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_campaigns_status                 ON campaigns(status);
CREATE INDEX IF NOT EXISTS idx_campaign_messages_campaign_id    ON campaign_messages(campaign_id);
CREATE INDEX IF NOT EXISTS idx_campaign_messages_lead_unique_key ON campaign_messages(lead_unique_key);
CREATE INDEX IF NOT EXISTS idx_campaign_messages_status         ON campaign_messages(status);

-- =============================================================================
-- Row Level Security
--
-- Backend uses SUPABASE_SERVICE_ROLE_KEY which bypasses RLS. All anon/
-- authenticated traffic is denied by default. Frontend MUST call the backend
-- API (no direct supabase.from() reads from the browser).
-- =============================================================================

ALTER TABLE leads               ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaigns           ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaign_messages   ENABLE ROW LEVEL SECURITY;
ALTER TABLE orchestration_jobs  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON leads, campaigns, campaign_messages, orchestration_jobs FROM anon;
REVOKE ALL ON leads, campaigns, campaign_messages, orchestration_jobs FROM authenticated;
REVOKE ALL ON leads, campaigns, campaign_messages, orchestration_jobs FROM PUBLIC;
-- account_deletions REVOKE statements are deferred until after the table is
-- created further down (see "GDPR Article 17" section) so the file stays
-- applicable top-to-bottom on a fresh project — REVOKE on a not-yet-created
-- relation would error otherwise.

-- Defense-in-depth: deny-all policies declared AS RESTRICTIVE so they AND
-- with any future PERMISSIVE policy. A future ad-hoc PERMISSIVE qual=true
-- policy added in Supabase Studio cannot OR over a RESTRICTIVE qual=false
-- (whereas a default PERMISSIVE deny-all could). service_role bypasses RLS
-- so the backend is unaffected. account_deletions uses the same mode (see
-- block below) so all 5 core tables share identical defense-in-depth.
DROP POLICY IF EXISTS leads_deny_all              ON leads;
DROP POLICY IF EXISTS campaigns_deny_all          ON campaigns;
DROP POLICY IF EXISTS campaign_messages_deny_all  ON campaign_messages;
DROP POLICY IF EXISTS orchestration_jobs_deny_all ON orchestration_jobs;

CREATE POLICY leads_deny_all              ON leads              AS RESTRICTIVE FOR ALL TO anon, authenticated USING (false) WITH CHECK (false);
CREATE POLICY campaigns_deny_all          ON campaigns          AS RESTRICTIVE FOR ALL TO anon, authenticated USING (false) WITH CHECK (false);
CREATE POLICY campaign_messages_deny_all  ON campaign_messages  AS RESTRICTIVE FOR ALL TO anon, authenticated USING (false) WITH CHECK (false);
CREATE POLICY orchestration_jobs_deny_all ON orchestration_jobs AS RESTRICTIVE FOR ALL TO anon, authenticated USING (false) WITH CHECK (false);

-- =============================================================================
-- Narrow schema-migration RPC (replaces generic exec_sql)
-- =============================================================================
CREATE OR REPLACE FUNCTION add_lead_column(col text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
-- `pg_catalog` first prevents shadowing of `format`/built-ins by a
-- malicious same-name function in `public`. SECURITY DEFINER runs as the
-- function owner, so any search_path hijack would inherit that authority.
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF col IS NULL OR col !~ '^[A-Za-z_][A-Za-z0-9_]{0,62}$' THEN
    RAISE EXCEPTION 'invalid column name';
  END IF;
  EXECUTE format('ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS %I TEXT', col);
END
$$;

REVOKE EXECUTE ON FUNCTION add_lead_column(text) FROM anon, authenticated, public;
-- service_role bypasses GRANTs implicitly, so backend can still call this.

-- Pin the function to the superuser/postgres owner so the SECURITY DEFINER
-- authority can't be downgraded by a re-deploy under a less-trusted role.
ALTER FUNCTION add_lead_column(text) OWNER TO postgres;

-- Block any role from creating shadowing objects in `public` (function,
-- table, view) that could collide with built-in identifiers resolved via
-- search_path inside SECURITY DEFINER functions.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- =============================================================================
-- updated_at refresh trigger
--
-- Lives in the schema file (was previously only in the live DB — fresh apply
-- produced tables where updated_at never advanced after INSERT). Listed in
-- src/scripts/check_function_safety.py::EXPECTED_FUNCTIONS, so the daily
-- function-safety audit asserts ownership + search_path here.
-- =============================================================================
CREATE OR REPLACE FUNCTION public.update_updated_at_column()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
  NEW.updated_at = timezone('utc'::text, now());
  RETURN NEW;
END
$$;

ALTER FUNCTION public.update_updated_at_column() OWNER TO postgres;
REVOKE EXECUTE ON FUNCTION public.update_updated_at_column() FROM anon, authenticated, public;

DROP TRIGGER IF EXISTS leads_updated_at_trg              ON leads;
DROP TRIGGER IF EXISTS orchestration_jobs_updated_at_trg ON orchestration_jobs;
DROP TRIGGER IF EXISTS campaigns_updated_at_trg          ON campaigns;

CREATE TRIGGER leads_updated_at_trg
    BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();
CREATE TRIGGER orchestration_jobs_updated_at_trg
    BEFORE UPDATE ON orchestration_jobs
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();
CREATE TRIGGER campaigns_updated_at_trg
    BEFORE UPDATE ON campaigns
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

-- =============================================================================
-- Live-state reconciliation (additive, forward-only)
--
-- The production "Lead Scraper" Supabase project has accumulated columns over
-- time that are not declared in the original CREATE TABLE above. The E2E run
-- on 2026-05-20 surfaced the drift (see E2E_TEST_REPORT.md, bug B2). The
-- statements below make a fresh project apply equivalent additions so the
-- two schemas converge, without dropping anything from existing projects.
--
-- All `ADD COLUMN IF NOT EXISTS` so re-running the file is idempotent. None
-- of these columns are referenced by current backend code paths — they are
-- declared here as documentation of live reality, not as new requirements.
-- If a follow-up audit confirms they are dead, replace with `DROP COLUMN IF
-- EXISTS` in a separate, intentional migration.
-- =============================================================================
ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS phone_number          TEXT;
ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS campaign_segment      TEXT;
ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS business_summary      TEXT;
ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS business_description  TEXT;
ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS company_description   TEXT;

-- =============================================================================
-- Demo-data flag (Phase 13.3).
--
-- Seeded by src/scripts/seed_demo_data.py; default FALSE so every existing
-- row + every real producer (CSV upload, Google-Maps scrape, enrichment)
-- keeps is_demo=false without changes. The frontend's "Show demo data"
-- toggle defaults OFF — backend /leads + /stats filter is_demo=false
-- unless ?include_demo=true. DELETE /leads/demo (admin-token-gated) wipes
-- only rows where is_demo=true (and the campaign_messages that reference
-- them via lead_unique_key).
--
-- Partial index keeps the cardinality cost near zero (only TRUE rows
-- carry an index entry; the bulk of the table is FALSE).
-- =============================================================================
ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS is_demo BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_leads_is_demo ON public.leads (is_demo) WHERE is_demo = TRUE;

-- =============================================================================
-- DB-level CHECK constraints (defense in depth)
--
-- Pydantic at the FastAPI boundary already validates these, but Supabase
-- Studio + the service_role key bypass that layer. Allowlists below mirror
-- current producer output verified by grep across `src/` + `backend/`. The
-- `schema_drift_check.py` CI gate asserts every named constraint below
-- exists in the live DB.
--
-- `DO $$ ... EXCEPTION WHEN duplicate_object` lets a fresh apply skip a
-- constraint that's already present (PostgreSQL has no `ADD CONSTRAINT IF
-- NOT EXISTS` for CHECK — only column-level constraints support that).
-- =============================================================================
DO $$ BEGIN
  ALTER TABLE public.leads
    ADD CONSTRAINT leads_seo_score_range
    CHECK (seo_score IS NULL OR (seo_score BETWEEN 0 AND 100));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE public.leads
    ADD CONSTRAINT leads_outreach_score_range
    CHECK (outreach_score IS NULL OR (outreach_score BETWEEN 0 AND 100));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- audit_status wide allowlist. Code (src/core/parallel_auditor.py) writes
-- 'Pending'/'Completed'/'Failed' + error-reason strings ('Timeout',
-- '403 Forbidden', '404 Not Found', 'Invalid URL'). 'Processing' kept
-- for forward-compat — schema previously declared it; no producer writes
-- it today. Refactoring the error-reason values into `last_error` would
-- let us shrink the allowlist; tracked separately.
DO $$ BEGIN
  ALTER TABLE public.leads
    ADD CONSTRAINT leads_audit_status_allowed
    CHECK (audit_status IS NULL OR audit_status IN (
      'Pending', 'Processing', 'Completed', 'Failed',
      'Timeout', '403 Forbidden', '404 Not Found', 'Invalid URL'
    ));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- enrichment_status uppercase per src/scrapers/enrichment_engine.py +
-- src/core/parallel_auditor.py. Default 'PENDING'.
DO $$ BEGIN
  ALTER TABLE public.leads
    ADD CONSTRAINT leads_enrichment_status_allowed
    CHECK (enrichment_status IS NULL OR enrichment_status IN (
      'PENDING', 'COMPLETED', 'FAILED', 'FAILED_NO_CONTENT'
    ));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Loose email shape. Intentionally less strict than the SMTP sender's
-- regex (src/integrations/email_sender.py uses
-- `^[^@\s]+@[^@\s]+\.[^@\s]+$` — header-injection guard at boundary).
-- DB rejects only obviously broken values so ingest doesn't fail on
-- quirky-but-valid scraped emails.
DO $$ BEGIN
  ALTER TABLE public.leads
    ADD CONSTRAINT leads_email_basic_shape
    CHECK (email IS NULL OR (length(email) >= 3 AND email LIKE '%@%'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE public.orchestration_jobs
    ADD CONSTRAINT orchestration_jobs_status_allowed
    CHECK (status IN (
      'starting', 'running', 'completed', 'failed', 'stopped'
    ));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE public.campaigns
    ADD CONSTRAINT campaigns_channel_allowed
    CHECK (channel IN ('email', 'linkedin', 'multi'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE public.campaigns
    ADD CONSTRAINT campaigns_status_allowed
    CHECK (status IS NULL OR status IN (
      'draft', 'active', 'paused', 'completed'
    ));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE public.campaign_messages
    ADD CONSTRAINT campaign_messages_channel_allowed
    CHECK (channel IN ('email', 'linkedin', 'multi'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE public.campaign_messages
    ADD CONSTRAINT campaign_messages_status_allowed
    CHECK (status IS NULL OR status IN (
      'pending', 'sent', 'delivered', 'replied', 'bounced'
    ));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ===========================================================================
-- GDPR Article 17 (right to erasure) audit trail.
--
-- One row per DELETE /operator/account invocation. Retained for 30 days
-- (fraud / contested-deletion window), then purged by
-- src/scripts/purge_expired_audit_log.py (security.yml daily cron).
--
-- Schema is intentionally narrow — operator_email + remote_ip + row
-- counts snapshot are enough to trace "who, when, from where, what was
-- wiped" without holding any deleted business data (which would defeat
-- the erasure right).
-- ===========================================================================
CREATE TABLE IF NOT EXISTS public.account_deletions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deleted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    operator_email TEXT,
    remote_ip     TEXT,
    row_counts    JSONB NOT NULL,
    expires_at    TIMESTAMPTZ NOT NULL
);

-- Index supports the daily purge query.
CREATE INDEX IF NOT EXISTS idx_account_deletions_expires_at
    ON public.account_deletions (expires_at);

-- RLS: deny-all (matches the 4 core tables — only service_role bypasses).
ALTER TABLE public.account_deletions ENABLE ROW LEVEL SECURITY;

-- Defense-in-depth GRANT revoke (parity with the upper REVOKE block on the
-- 4 core tables). Declared after the table exists so the file applies
-- top-to-bottom on a fresh project.
REVOKE ALL ON public.account_deletions FROM anon, authenticated, PUBLIC;

DO $$ BEGIN
    CREATE POLICY account_deletions_deny_all ON public.account_deletions
        AS RESTRICTIVE
        FOR ALL
        TO anon, authenticated
        USING (false)
        WITH CHECK (false);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ===========================================================================
-- Email dispatch — provider_message_id, send ledger, suppression list
--
-- Backs the Resend HTTP API path (docs/email-dispatch-architecture.md §2.4).
-- Three additions:
--   1. campaign_messages.provider_message_id + bounce_reason — written when
--      the dispatcher receives Resend's 200 response (provider_message_id =
--      Resend's msg_id, used by the /webhooks/resend handler to map
--      delivered/bounced events back to the source row).
--   2. email_send_ledger — append-only per-domain throttle ledger. The
--      dispatcher consults it before sending to avoid same-domain bursts
--      (Gmail/Outlook penalize) — currently 3/hr/domain target.
--   3. suppressions — opt-out + bounce list (renamed from email_suppression
--      in Phase 14.2 to support multi-channel identifiers). dispatcher
--      SKIPs any row whose (identifier_type, identifier_value, channel)
--      matches. Webhook populates on `email.bounced` / `email.complained`
--      / unsubscribe events. Generic shape unblocks LinkedIn (Phase 17.x).
--
-- All three additive — no destructive changes to existing tables. RLS
-- deny-all + REVOKE on anon/authenticated/PUBLIC mirror the 5-table
-- pattern above (only service_role bypasses).
-- ===========================================================================
ALTER TABLE public.campaign_messages
    ADD COLUMN IF NOT EXISTS provider_message_id TEXT;
ALTER TABLE public.campaign_messages
    ADD COLUMN IF NOT EXISTS bounce_reason TEXT;

-- Partial index — only sent rows carry a provider_message_id, so we skip
-- the NULL pile (pending rows). Webhook lookup is by exact msg_id.
CREATE INDEX IF NOT EXISTS idx_campaign_messages_provider_message_id
    ON public.campaign_messages(provider_message_id)
    WHERE provider_message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.email_send_ledger (
    id               BIGSERIAL PRIMARY KEY,
    recipient_domain TEXT NOT NULL,
    sent_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Index supports the throttle predicate
--   `WHERE recipient_domain = ? AND sent_at > now() - interval '1 hour'`.
CREATE INDEX IF NOT EXISTS idx_email_send_ledger_domain_sent
    ON public.email_send_ledger(recipient_domain, sent_at DESC);

ALTER TABLE public.email_send_ledger ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.email_send_ledger FROM anon, authenticated, PUBLIC;

DO $$ BEGIN
    CREATE POLICY email_send_ledger_deny_all ON public.email_send_ledger
        AS RESTRICTIVE
        FOR ALL
        TO anon, authenticated
        USING (false)
        WITH CHECK (false);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ===========================================================================
-- Phase 14.0 — multi-dispatcher provider columns (additive to PR #286)
-- ---------------------------------------------------------------------------
-- Adds `provider` to email_send_ledger so the dispatcher can throttle
-- per-provider AND maintain per-provider cost accounting. The forensic
-- provider column on the suppression table (formerly
-- email_suppression.source, renamed to suppressions.source_provider in
-- Phase 14.2) is declared in the suppressions section above. LinkedIn
-- (HeyReach) is included in the provider
-- allowlist because the LinkedIn-surface decision (parallel tables vs
-- email_*→outreach_* rename) is deferred to Phase 17.0 — until then,
-- HeyReach writes land here, which is functionally fine: domain/email
-- columns become NULL for LinkedIn sends, so we also relax
-- recipient_domain NOT NULL (was incompatible with the LinkedIn path).
-- ===========================================================================

ALTER TABLE public.email_send_ledger
    ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'resend';

-- HeyReach (LinkedIn) sends have no recipient_domain; relax the NOT NULL
-- so the dispatcher can write a provider-tagged ledger row without a
-- synthetic placeholder. Per-email throttling for LinkedIn moves to
-- Phase 17.0 with the dedicated linkedin_send_ledger table.
ALTER TABLE public.email_send_ledger
    ALTER COLUMN recipient_domain DROP NOT NULL;

DO $$ BEGIN
    ALTER TABLE public.email_send_ledger
        ADD CONSTRAINT email_send_ledger_provider_allowed
        CHECK (provider IN ('resend', 'instantly', 'smtp', 'heyreach'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Throttle queries by (provider, domain, time) — composite index ahead
-- of the existing single-column index so per-provider throttle predicates
-- ("3/hr/domain on Resend, separate quota on Instantly") use an index scan.
CREATE INDEX IF NOT EXISTS idx_email_send_ledger_provider_domain_sent
    ON public.email_send_ledger (provider, recipient_domain, sent_at DESC);

-- ===========================================================================
-- Phase 14.2 — generic multi-channel suppressions table
-- ---------------------------------------------------------------------------
-- Renames email_suppression → suppressions and extends to multi-channel
-- identifiers (email|domain|linkedin_url|phone × channel∈{email|linkedin|
-- sms|all}). The existing dispatcher precheck in
-- src/integrations/instantly_sender.py is rewired to predicate on
-- identifier_type='email' AND channel IN ('email','all') against the
-- renamed table — additive, no schema-side data loss.
--
-- Why now: persistent suppression is the #1 blocker before any live cold
-- send. In-process bounced_emails set is restart-fragile; one Render
-- redeploy mid-campaign torches domain reputation. Webhook handler (PR γ)
-- INSERTs into this table on every bounce/unsub event so /webhooks/
-- instantly is idempotent + restart-safe.
--
-- Migration path:
--   * Fresh DB → CREATE TABLE IF NOT EXISTS creates new shape directly;
--     DO $$ rename block no-ops (email_suppression doesn't exist).
--   * Live DB (Phase 14.0/14.1 active) → DO $$ rename block fires,
--     existing rows backfill via column defaults (identifier_type='email',
--     channel='email'); CREATE TABLE IF NOT EXISTS no-ops; ADD COLUMN IF
--     NOT EXISTS adds the new columns; constraint DROP+ADD swaps the
--     reason allowlist + adds the multi-channel CHECKs.
--
-- Reason allowlist extended from {bounce,complaint,manual} to include:
--   * bounce_hard / bounce_soft_3x — webhook taxonomy (Instantly + Resend)
--   * unsubscribe — RFC 8058 List-Unsubscribe-Post (PR β)
--   * gdpr_request — Article 17 erasure suppression
--   * spam_trap — operator-initiated after seed-list bounce
-- Old values ('bounce', 'complaint') stay in the allowlist so existing
-- rows survive the constraint swap.
-- ===========================================================================

-- 1. Rename + restructure existing email_suppression if it exists (live DB).
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema='public' AND table_name='email_suppression'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema='public' AND table_name='suppressions'
    ) THEN
        ALTER TABLE public.email_suppression RENAME TO suppressions;
        ALTER TABLE public.suppressions RENAME COLUMN email TO identifier_value;
        ALTER TABLE public.suppressions RENAME COLUMN added_at TO created_at;
        -- `source` column was added in Phase 14.0; rename to spec name.
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='suppressions'
              AND column_name='source'
        ) THEN
            ALTER TABLE public.suppressions RENAME COLUMN source TO source_provider;
        END IF;
        -- Old PK was on `email` (now `identifier_value`); drop so (id) can be PK.
        ALTER TABLE public.suppressions DROP CONSTRAINT IF EXISTS email_suppression_pkey;
        -- Old CHECK constraints survive RENAME with the old name; drop them so
        -- the new wider allowlist replaces them cleanly below.
        ALTER TABLE public.suppressions DROP CONSTRAINT IF EXISTS email_suppression_reason_allowed;
        ALTER TABLE public.suppressions DROP CONSTRAINT IF EXISTS email_suppression_source_allowed;
        -- Old policy survives RENAME with its old name; rename to match new table.
        ALTER POLICY email_suppression_deny_all ON public.suppressions RENAME TO suppressions_deny_all;
    END IF;
EXCEPTION WHEN undefined_object THEN NULL; END $$;

-- 2. End-state shape. For fresh DBs this is the authoritative CREATE; for
--    upgraded DBs the rename above has already produced the table and the
--    IF NOT EXISTS clauses make this a no-op.
CREATE TABLE IF NOT EXISTS public.suppressions (
    id                BIGSERIAL PRIMARY KEY,
    identifier_type   TEXT NOT NULL DEFAULT 'email',
    identifier_value  TEXT NOT NULL,
    reason            TEXT NOT NULL,
    channel           TEXT NOT NULL DEFAULT 'email',
    source_provider   TEXT,
    source_campaign_id UUID REFERENCES public.campaigns(id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by        TEXT,
    notes             TEXT
);

-- 3. Additive columns for upgraded DBs (no-op on fresh DBs where CREATE
--    above already added them).
ALTER TABLE public.suppressions
    ADD COLUMN IF NOT EXISTS id BIGSERIAL,
    ADD COLUMN IF NOT EXISTS identifier_type TEXT NOT NULL DEFAULT 'email',
    ADD COLUMN IF NOT EXISTS channel TEXT NOT NULL DEFAULT 'email',
    ADD COLUMN IF NOT EXISTS source_provider TEXT,
    ADD COLUMN IF NOT EXISTS source_campaign_id UUID REFERENCES public.campaigns(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS created_by TEXT,
    ADD COLUMN IF NOT EXISTS notes TEXT;

-- Upgraded DBs land here without a PK (we dropped email_suppression_pkey
-- above). Attach (id) PK if missing. Fresh DBs already got the PK via the
-- CREATE TABLE statement.
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_schema='public' AND table_name='suppressions'
          AND constraint_type='PRIMARY KEY'
    ) THEN
        ALTER TABLE public.suppressions ADD CONSTRAINT suppressions_pkey PRIMARY KEY (id);
    END IF;
END $$;

-- 4. CHECK constraint suite. Each in its own DO block so a single rerun
--    on a partially-migrated DB recovers idempotently.
DO $$ BEGIN
    ALTER TABLE public.suppressions
        ADD CONSTRAINT suppressions_identifier_type_allowed
        CHECK (identifier_type IN ('email', 'domain', 'linkedin_url', 'phone'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.suppressions
        ADD CONSTRAINT suppressions_reason_allowed
        CHECK (reason IN ('bounce', 'bounce_hard', 'bounce_soft_3x', 'complaint',
                          'manual', 'unsubscribe', 'gdpr_request', 'spam_trap'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.suppressions
        ADD CONSTRAINT suppressions_channel_allowed
        CHECK (channel IN ('email', 'linkedin', 'sms', 'all'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.suppressions
        ADD CONSTRAINT suppressions_provider_allowed
        CHECK (source_provider IS NULL OR
               source_provider IN ('resend', 'instantly', 'smtp', 'heyreach', 'manual'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- (identifier_type, identifier_value, channel) is the natural lookup key.
-- Replaces the implicit uniqueness the old email PRIMARY KEY provided.
DO $$ BEGIN
    ALTER TABLE public.suppressions
        ADD CONSTRAINT suppressions_unique
        UNIQUE (identifier_type, identifier_value, channel);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- 5. Hot-path index for the dispatcher precheck — partial index on
--    channel∈{email,all} (the email dispatcher's only lookup shape).
--    LinkedIn dispatcher (Phase 17.x) gets a sibling partial index.
CREATE INDEX IF NOT EXISTS idx_suppressions_lookup
    ON public.suppressions (identifier_value, channel)
    WHERE channel IN ('email', 'all');

-- 6. RLS deny-all (matches the canonical pattern; upgraded DBs already
--    have RLS enabled + policy renamed above — re-running is idempotent).
ALTER TABLE public.suppressions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.suppressions FROM anon, authenticated, PUBLIC;

DO $$ BEGIN
    CREATE POLICY suppressions_deny_all ON public.suppressions
        AS RESTRICTIVE
        FOR ALL
        TO anon, authenticated
        USING (false)
        WITH CHECK (false);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

COMMENT ON COLUMN public.suppressions.source_provider IS
    'Provider that reported the suppression (resend|instantly|smtp|heyreach|manual). NULL only on legacy rows from pre-14.0; new rows always set a value. Enforced by suppressions_provider_allowed CHECK.';
COMMENT ON COLUMN public.suppressions.identifier_type IS
    'Channel-independent identifier kind. ''email'' is the only producer today; ''domain'' / ''linkedin_url'' / ''phone'' reserved for Phase 17.x.';

-- ===========================================================================
-- Phase 14.2 (PR β) — campaign_messages thread / idempotency columns
-- ---------------------------------------------------------------------------
-- Three additions on campaign_messages, each backed by a partial index
-- on its only lookup shape:
--
--   * thread_id              — provider-side conversation key (Instantly
--                              "campaign_step" || "lead_email" pair). The
--                              webhook handler (PR γ) joins on this to roll
--                              up email_replied events to the campaign.
--   * in_reply_to_message_id — points at a previously-sent message in the
--                              same thread; nullable on the initial touch.
--                              Lets reply-classifier (Phase 16) walk the
--                              chain backwards to fetch context.
--   * tracking_id            — LDS-side opaque UUID, minted on row creation,
--                              used by the unsubscribe-link signer to bind
--                              the token to a specific outbound message
--                              without leaking the row id. PostgREST never
--                              exposes id directly; tracking_id can be
--                              embedded in a public URL safely.
--
-- All three additive — no destructive changes. No CHECK constraints (free-
-- form opaque IDs from third-party providers). UNIQUE on tracking_id is
-- added because it's the only field the webhook / unsubscribe handler can
-- use to look up an LDS row without trusting the (mutable) email recipient
-- string.
-- ===========================================================================

-- One ALTER per column so schema_drift_check.py's regex
-- (single-column `ALTER TABLE ... ADD COLUMN <name>`) sees each
-- declaration; the multi-clause form is parsed as a single match and
-- skips the later columns silently.
ALTER TABLE public.campaign_messages
    ADD COLUMN IF NOT EXISTS thread_id TEXT;
ALTER TABLE public.campaign_messages
    ADD COLUMN IF NOT EXISTS in_reply_to_message_id TEXT;
ALTER TABLE public.campaign_messages
    ADD COLUMN IF NOT EXISTS tracking_id UUID DEFAULT gen_random_uuid();

-- Backfill pre-existing rows (DEFAULT does not apply retroactively).
UPDATE public.campaign_messages
   SET tracking_id = gen_random_uuid()
 WHERE tracking_id IS NULL;

DO $$ BEGIN
    ALTER TABLE public.campaign_messages
        ADD CONSTRAINT campaign_messages_tracking_id_unique
        UNIQUE (tracking_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Partial indexes — both nullable columns are NULL on most rows (pending /
-- initial-touch / no-thread-yet); partial-index skips the NULL pile.
CREATE INDEX IF NOT EXISTS idx_campaign_messages_thread_id
    ON public.campaign_messages(thread_id)
    WHERE thread_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_campaign_messages_in_reply_to
    ON public.campaign_messages(in_reply_to_message_id)
    WHERE in_reply_to_message_id IS NOT NULL;

COMMENT ON COLUMN public.campaign_messages.thread_id IS
    'Provider-side conversation key (Instantly/HeyReach). Used by the webhook handler to attribute replies back to the originating thread. NULL on the first touch of a new thread.';
COMMENT ON COLUMN public.campaign_messages.in_reply_to_message_id IS
    'Points at the previously-sent message in the same thread; NULL on the initial touch. Enables reply-classifier (Phase 16) to walk the chain backwards.';
COMMENT ON COLUMN public.campaign_messages.tracking_id IS
    'LDS-side opaque UUID for unsubscribe-link binding. Embedded in the RFC 8058 List-Unsubscribe token (HMAC-signed) so the unsubscribe handler can suppress the right (lead_unique_key, channel) tuple without trusting the recipient-side email value.';

-- ===========================================================================
-- Phase 14.2 (PR γ) — webhook_events idempotency table
-- ---------------------------------------------------------------------------
-- Provider webhooks (Instantly today; Resend / HeyReach next) MUST be
-- idempotent at the handler boundary because:
--   * Instantly retries on any non-2xx, on timeout (>2s default), and
--     occasionally even on confirmed 2xx delivery.
--   * Network blips between TLS handshake completion and our INSERT
--     can leave the provider believing the event wasn't ack'd.
--   * Replay attacks: a leaked or sniffed valid-signature webhook body
--     can be re-played by anyone with TCP access to the public URL.
--
-- The (provider, event_id) UNIQUE constraint is the idempotency lock:
-- a duplicate INSERT collides, the handler reads the 23505 error code
-- and returns 200 OK without re-running the side effects.
--
-- payload JSONB stores the verified raw body for replay/audit. No TTL
-- enforcement yet — partial-vacuum job lands when volume justifies it.
--
-- processed_at / processing_error give the background processor a
-- place to checkpoint without a separate state table. The partial
-- index idx_webhook_events_unprocessed supports the worker's "pick
-- the next one" scan.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS public.webhook_events (
    id                BIGSERIAL PRIMARY KEY,
    provider          TEXT NOT NULL,
    event_id          TEXT NOT NULL,
    event_type        TEXT NOT NULL,
    payload           JSONB NOT NULL,
    received_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at      TIMESTAMPTZ,
    processing_error  TEXT
);

DO $$ BEGIN
    ALTER TABLE public.webhook_events
        ADD CONSTRAINT webhook_events_provider_allowed
        CHECK (provider IN ('instantly', 'resend', 'heyreach'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.webhook_events
        ADD CONSTRAINT webhook_events_idempotency
        UNIQUE (provider, event_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_webhook_events_unprocessed
    ON public.webhook_events (provider, received_at)
    WHERE processed_at IS NULL;

ALTER TABLE public.webhook_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.webhook_events FROM anon, authenticated, PUBLIC;

DO $$ BEGIN
    CREATE POLICY webhook_events_deny_all ON public.webhook_events
        AS RESTRICTIVE
        FOR ALL
        TO anon, authenticated
        USING (false)
        WITH CHECK (false);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

COMMENT ON COLUMN public.webhook_events.event_id IS
    'Provider-side event identifier. Forms the (provider, event_id) UNIQUE pair that gives the handler idempotency under retries + replay attacks. Instantly: event UUID from webhook body. Resend/HeyReach: provider-specific.';
COMMENT ON COLUMN public.webhook_events.payload IS
    'Verified raw webhook body. Stored as JSONB for replay/audit; a future partial-vacuum job (Phase 14.3+) trims older rows once volume justifies the maintenance window.';

-- ---------------------------------------------------------------------------
-- Phase 14.2 (PR γ) — extend campaign_messages.status allowlist with
-- 'unsubscribed'. The webhook handler stamps this status alongside the
-- suppression INSERT so operator reporting ("which message got the
-- unsubscribe") works without a join. DROP+ADD with the same name so
-- the drift gate doesn't see two CHECK constraints.
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    ALTER TABLE public.campaign_messages
        DROP CONSTRAINT IF EXISTS campaign_messages_status_allowed;
EXCEPTION WHEN undefined_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.campaign_messages
        ADD CONSTRAINT campaign_messages_status_allowed
        CHECK (status IS NULL OR status IN (
            'pending', 'sent', 'delivered', 'replied', 'bounced', 'unsubscribed'
        ));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ===========================================================================
-- Phase 15.1 — sequencing engine tables
-- ---------------------------------------------------------------------------
-- Three new tables drive multi-step / A-B-variant cold outreach:
--
--   * sequences         — top-level container per campaign. Status drives
--                         whether dispatch_tick picks up its steps.
--   * sequence_steps    — ordered (sequence_id, step_index) tuple with
--                         per-step delay, send-window, branch condition.
--                         Channel ∈ {email, linkedin}; LinkedIn dispatch
--                         lands in Phase 17.
--   * sequence_variants — A/B copy split per step. Weighted random
--                         selection at dispatch time; variant_label ∈
--                         [A..Z] gives 26 max per step (plenty in
--                         practice — researched cap is 3-5).
--
-- campaign_messages gets two FK columns (step_id, variant_id) + two
-- scheduling fields (scheduled_at, dispatched_at). The dispatch_tick
-- worker (Phase 15.2) consumes the partial index
-- idx_campaign_messages_dispatch_queue to find due messages without a
-- seq scan as the table grows.
--
-- All three tables share the canonical RLS deny-all + REVOKE posture
-- (anon/authenticated/PUBLIC have zero access; service_role bypasses).
--
-- Research-pinned design points captured here so reviewers don't have
-- to re-derive:
--   * 4-5 steps over 14-21 days = empirical sweet spot
--   * Day 1/3/7/14/21 cadence (widening, not uniform)
--   * Steps 1-3 share a thread (blank subject → "Re:" continuation);
--     step 4+ optionally breaks. Modeled via sequence_steps
--     .thread_with_prior boolean.
--   * Most replies in steps 2-4, not step 1.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS public.sequences (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID NOT NULL REFERENCES public.campaigns(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'draft',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    ALTER TABLE public.sequences
        ADD CONSTRAINT sequences_status_allowed
        CHECK (status IN ('draft', 'active', 'paused', 'archived'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_sequences_campaign_active
    ON public.sequences (campaign_id, status)
    WHERE status = 'active';

CREATE TABLE IF NOT EXISTS public.sequence_steps (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sequence_id         UUID NOT NULL REFERENCES public.sequences(id) ON DELETE CASCADE,
    step_index          INT NOT NULL,
    channel             TEXT NOT NULL,
    delay_days          INT NOT NULL DEFAULT 0,
    delay_hours         INT NOT NULL DEFAULT 0,
    thread_with_prior   BOOLEAN NOT NULL DEFAULT false,
    branch_condition    TEXT NOT NULL DEFAULT 'always',
    send_window_start   TIME NOT NULL DEFAULT '09:00',
    send_window_end     TIME NOT NULL DEFAULT '17:00',
    send_days           TEXT NOT NULL DEFAULT 'mon,tue,wed,thu,fri',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    ALTER TABLE public.sequence_steps
        ADD CONSTRAINT sequence_steps_channel_allowed
        CHECK (channel IN ('email', 'linkedin'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.sequence_steps
        ADD CONSTRAINT sequence_steps_branch_allowed
        CHECK (branch_condition IN (
            'always', 'no_reply', 'no_open',
            'connection_accepted', 'replied'
        ));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.sequence_steps
        ADD CONSTRAINT sequence_steps_delay_nonneg
        CHECK (delay_days >= 0 AND delay_hours >= 0);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.sequence_steps
        ADD CONSTRAINT sequence_steps_unique_index
        UNIQUE (sequence_id, step_index);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_sequence_steps_lookup
    ON public.sequence_steps (sequence_id, step_index);

CREATE TABLE IF NOT EXISTS public.sequence_variants (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    step_id           UUID NOT NULL REFERENCES public.sequence_steps(id) ON DELETE CASCADE,
    variant_label     TEXT NOT NULL,
    subject_template  TEXT,
    body_template     TEXT NOT NULL,
    weight            INT NOT NULL DEFAULT 50,
    ai_model_used     TEXT,
    ai_prompt_version TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    ALTER TABLE public.sequence_variants
        ADD CONSTRAINT sequence_variants_weight_positive
        CHECK (weight > 0);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Single uppercase letter — A..Z gives 26 variants per step, well above
-- any realistic A/B/C/D fan-out. Drives consistent labeling across UI +
-- logs + analytics queries.
DO $$ BEGIN
    ALTER TABLE public.sequence_variants
        ADD CONSTRAINT sequence_variants_label_format
        CHECK (variant_label ~ '^[A-Z]$');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.sequence_variants
        ADD CONSTRAINT sequence_variants_unique_label
        UNIQUE (step_id, variant_label);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------------------------------------------------------------------------
-- campaign_messages: add step + variant FK refs plus scheduling fields.
-- One ALTER per column so the drift gate's single-column regex catches
-- each declaration (multi-clause ALTER only matches the first — pinned
-- lesson from Phase 14.2).
-- ---------------------------------------------------------------------------
ALTER TABLE public.campaign_messages
    ADD COLUMN IF NOT EXISTS step_id UUID REFERENCES public.sequence_steps(id);
ALTER TABLE public.campaign_messages
    ADD COLUMN IF NOT EXISTS variant_id UUID REFERENCES public.sequence_variants(id);
ALTER TABLE public.campaign_messages
    ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMPTZ;
ALTER TABLE public.campaign_messages
    ADD COLUMN IF NOT EXISTS dispatched_at TIMESTAMPTZ;

-- Hot-path index — dispatch_tick scans this every minute. Partial WHERE
-- skips the long tail of already-sent / failed rows.
CREATE INDEX IF NOT EXISTS idx_campaign_messages_dispatch_queue
    ON public.campaign_messages (scheduled_at)
    WHERE status = 'pending' AND scheduled_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- RLS deny-all (matches the canonical pattern across all core tables)
-- ---------------------------------------------------------------------------
ALTER TABLE public.sequences ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sequence_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sequence_variants ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.sequences, public.sequence_steps, public.sequence_variants
    FROM anon, authenticated, PUBLIC;

DO $$ BEGIN
    CREATE POLICY sequences_deny_all ON public.sequences
        AS RESTRICTIVE
        FOR ALL
        TO anon, authenticated
        USING (false)
        WITH CHECK (false);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE POLICY sequence_steps_deny_all ON public.sequence_steps
        AS RESTRICTIVE
        FOR ALL
        TO anon, authenticated
        USING (false)
        WITH CHECK (false);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE POLICY sequence_variants_deny_all ON public.sequence_variants
        AS RESTRICTIVE
        FOR ALL
        TO anon, authenticated
        USING (false)
        WITH CHECK (false);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

COMMENT ON COLUMN public.sequence_steps.thread_with_prior IS
    'When true, dispatch builds the send payload with empty subject + in_reply_to set to the prior step provider_message_id. Instantly + Resend interpret blank-subject as Re: <prior> continuation. Research-pinned: steps 1-3 thread, step 4+ optionally breaks.';
COMMENT ON COLUMN public.sequence_steps.branch_condition IS
    'always = advance unconditionally on any non-failure event. no_reply = advance only when prior step status reaches sent without replied. no_open = LinkedIn-only (no open detected by reminder). connection_accepted = LinkedIn-only Phase 17. replied = advance FROM reply event (positive-reply nurture branch).';
COMMENT ON COLUMN public.sequence_variants.weight IS
    'Variant selector (Phase 15.3) draws weighted-random from the per-step variant set. Weight is a positive integer; values are normalized by the selector. Default 50 = equal split for two A/B variants.';
COMMENT ON COLUMN public.campaign_messages.scheduled_at IS
    'When dispatch_tick should attempt the send. NULL on rows generated by the legacy single-shot path (pre-Phase 15.2). Partial index idx_campaign_messages_dispatch_queue covers (status=pending AND scheduled_at IS NOT NULL) for the dispatch worker hot path.';

-- ===========================================================================
-- Phase 15.2 — extend campaign_messages.status allowlist
-- ---------------------------------------------------------------------------
-- Three new states wired alongside the dispatch_tick worker:
--
--   * dispatching  — atomic-claim lock. The tick UPDATEs pending → dispatching
--                    with a status='pending' predicate; PG row-level locks
--                    serialize concurrent workers (two ticks racing for the
--                    same row → only one sees its UPDATE match). Released to
--                    'sent' by the dispatcher success path OR to 'pending' by
--                    the stale-claim sweeper after DISPATCH_CLAIM_TIMEOUT_MIN.
--   * cancelled    — written by the Phase 15.4 cancel-pending logic when a
--                    terminal event (bounced / unsubscribed / replied) on one
--                    step in a sequence invalidates downstream scheduled
--                    steps for the same lead.
--   * failed       — reserved for non-dispatcher failure paths (e.g. template
--                    render error in Phase 15.3, suppression-insertion race
--                    in the tick). Distinct from 'bounced' (= API-rejection
--                    or hard-bounce): a 'failed' row is a candidate for
--                    manual operator review, not an automatic re-queue.
--
-- DROP+ADD with the SAME constraint name so the drift gate continues to see
-- exactly one constraint named campaign_messages_status_allowed.
-- schema_drift_check.py EXPECTED_CHECK_CONSTRAINTS entry doesn't change.
-- ===========================================================================

DO $$ BEGIN
    ALTER TABLE public.campaign_messages
        DROP CONSTRAINT IF EXISTS campaign_messages_status_allowed;
EXCEPTION WHEN undefined_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.campaign_messages
        ADD CONSTRAINT campaign_messages_status_allowed
        CHECK (status IS NULL OR status IN (
            'pending', 'dispatching', 'sent', 'delivered',
            'replied', 'bounced', 'unsubscribed', 'cancelled', 'failed'
        ));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
