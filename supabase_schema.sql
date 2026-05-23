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
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc', now()),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc', now())
);

CREATE TABLE IF NOT EXISTS campaign_messages (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    campaign_id UUID REFERENCES campaigns(id) ON DELETE CASCADE,
    lead_unique_key TEXT REFERENCES leads(unique_key),
    channel TEXT NOT NULL,
    subject TEXT,
    body TEXT,
    status TEXT DEFAULT 'pending', -- pending, sent, delivered, replied, bounced
    sent_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc', now())
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

REVOKE ALL ON leads, campaigns, campaign_messages, orchestration_jobs, account_deletions FROM anon;
REVOKE ALL ON leads, campaigns, campaign_messages, orchestration_jobs, account_deletions FROM authenticated;
REVOKE ALL ON leads, campaigns, campaign_messages, orchestration_jobs, account_deletions FROM PUBLIC;

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
DO $$ BEGIN
    CREATE POLICY account_deletions_deny_all ON public.account_deletions
        AS RESTRICTIVE
        FOR ALL
        TO anon, authenticated
        USING (false)
        WITH CHECK (false);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
