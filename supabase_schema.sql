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

-- Index for faster lookups
CREATE INDEX IF NOT EXISTS idx_leads_unique_key ON leads(unique_key);
CREATE INDEX IF NOT EXISTS idx_leads_audit_status ON leads(audit_status);
CREATE INDEX IF NOT EXISTS idx_orchestration_jobs_status ON orchestration_jobs(status);

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

CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);
CREATE INDEX IF NOT EXISTS idx_campaign_messages_campaign_id ON campaign_messages(campaign_id);
CREATE INDEX IF NOT EXISTS idx_campaign_messages_status ON campaign_messages(status);

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

REVOKE ALL ON leads,              campaigns, campaign_messages, orchestration_jobs FROM anon;
REVOKE ALL ON leads,              campaigns, campaign_messages, orchestration_jobs FROM authenticated;

-- Defense-in-depth: even if a future ad-hoc GRANT in Supabase Studio re-adds
-- access to anon/authenticated, these explicit deny-all policies still block
-- every read/write. service_role bypasses RLS so the backend is unaffected.
DROP POLICY IF EXISTS leads_deny_all              ON leads;
DROP POLICY IF EXISTS campaigns_deny_all          ON campaigns;
DROP POLICY IF EXISTS campaign_messages_deny_all  ON campaign_messages;
DROP POLICY IF EXISTS orchestration_jobs_deny_all ON orchestration_jobs;

CREATE POLICY leads_deny_all              ON leads              FOR ALL TO anon, authenticated USING (false) WITH CHECK (false);
CREATE POLICY campaigns_deny_all          ON campaigns          FOR ALL TO anon, authenticated USING (false) WITH CHECK (false);
CREATE POLICY campaign_messages_deny_all  ON campaign_messages  FOR ALL TO anon, authenticated USING (false) WITH CHECK (false);
CREATE POLICY orchestration_jobs_deny_all ON orchestration_jobs FOR ALL TO anon, authenticated USING (false) WITH CHECK (false);

-- =============================================================================
-- Narrow schema-migration RPC (replaces generic exec_sql)
-- =============================================================================
CREATE OR REPLACE FUNCTION add_lead_column(col text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
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
