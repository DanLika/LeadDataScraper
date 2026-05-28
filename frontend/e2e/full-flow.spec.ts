import { test, expect, type Page } from '@playwright/test'
import { createClient, type SupabaseClient } from '@supabase/supabase-js'

// End-to-end pipeline spec. Heavyweight — drives real Google Maps scraping,
// real Gemini calls, real Supabase writes. Run against a throwaway Supabase
// project, not prod. Required env:
//   E2E_BASE_URL                 Next.js URL (default http://localhost:3000)
//   E2E_EMAIL, E2E_PASSWORD      Supabase Auth operator
//   E2E_SUPABASE_URL             same as backend SUPABASE_URL
//   E2E_SUPABASE_SERVICE_ROLE_KEY  service-role key for DB polling
//   E2E_BACKEND_URL              FastAPI direct URL (default http://127.0.0.1:8000)
//   E2E_API_KEY                  backend API_SECRET_KEY (for /leads/clear neg test)
//   E2E_ADMIN_TOKEN              backend ADMIN_TOKEN (for /leads/clear pos test)

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
const SUPABASE_URL = process.env.E2E_SUPABASE_URL || ''
const SUPABASE_SERVICE_ROLE_KEY = process.env.E2E_SUPABASE_SERVICE_ROLE_KEY || ''
const BACKEND_URL = (process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
const API_KEY = process.env.E2E_API_KEY || ''
const ADMIN_TOKEN = process.env.E2E_ADMIN_TOKEN || ''

const REQUIRED = { EMAIL, PASSWORD, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, API_KEY, ADMIN_TOKEN }
const MISSING = Object.entries(REQUIRED).filter(([, v]) => !v).map(([k]) => k)

test.skip(MISSING.length > 0, `Missing E2E env: ${MISSING.join(', ')}`)

test.describe.configure({ mode: 'serial' })

// Long timeouts — discovery alone took ~35s in CLAUDE.md's smoke flow,
// audits and Gemini calls each add minutes.
const TEST_TIMEOUT_MS = 20 * 60 * 1000
const DISCOVERY_POLL_TIMEOUT_MS = 8 * 60 * 1000
const AUDIT_POLL_TIMEOUT_MS = 10 * 60 * 1000
const HUNT_POLL_TIMEOUT_MS = 12 * 60 * 1000
const POLL_INTERVAL_MS = 4_000

const DISCOVERY_QUERY = 'dentists'
const DISCOVERY_LOCATION = 'Mostar'
const TARGET_LEAD_COUNT = 5

function admin(): SupabaseClient {
  return createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
    auth: { persistSession: false, autoRefreshToken: false },
  })
}

async function login(page: Page) {
  await page.goto('/login')
  await page.fill('input[name="email"]', EMAIL)
  await page.fill('input[name="password"]', PASSWORD)
  await Promise.all([
    page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15_000 }),
    page.click('button[type="submit"]'),
  ])
}

async function pollUntil<T>(
  fn: () => Promise<T | null>,
  timeoutMs: number,
  label: string,
): Promise<T> {
  const start = Date.now()

  while (true) {
    const v = await fn()
    if (v !== null && v !== undefined) return v
    if (Date.now() - start > timeoutMs) throw new Error(`Timeout waiting for ${label} after ${timeoutMs}ms`)
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS))
  }
}

async function countLeadsBySource(db: SupabaseClient, source: string): Promise<number> {
  const { count, error } = await db
    .from('leads')
    .select('*', { count: 'exact', head: true })
    .eq('lead_source', source)
  if (error) throw error
  return count ?? 0
}

test('full pipeline: discover → audit → hunt → outreach → campaign → CSV → /leads/clear', async ({ page }) => {
  test.setTimeout(TEST_TIMEOUT_MS)
  const db = admin()

  // ---------- 1) login ----------
  await login(page)
  await expect(page).not.toHaveURL(/\/login/)

  // ---------- 2) discovery ----------
  const googleMapsBefore = await countLeadsBySource(db, 'google_maps')
  const discoveryStartedAt = new Date().toISOString()

  // The dashboard owns the modal — open via query param per the
  // cross-page-navigation contract in CLAUDE.md.
  await page.goto('/?openDiscovery=1')
  await expect(page.locator('#discovery-query')).toBeVisible()
  await page.fill('#discovery-query', DISCOVERY_QUERY)
  await page.fill('#discovery-location', DISCOVERY_LOCATION)
  await page.click('button:has-text("Start Deep Search")')

  // ---------- 3) poll orchestration_jobs ----------
  const discoveryJob = await pollUntil(
    async () => {
      const { data, error } = await db
        .from('orchestration_jobs')
        .select('id, status, type, started_at, completed_at')
        .eq('type', 'discovery')
        .gte('started_at', discoveryStartedAt)
        .order('started_at', { ascending: false })
        .limit(1)
      if (error) throw error
      const row = data?.[0]
      if (row && (row.status === 'completed' || row.status === 'failed')) return row
      return null
    },
    DISCOVERY_POLL_TIMEOUT_MS,
    'orchestration_jobs.discovery completion',
  )
  expect(discoveryJob.status, 'discovery job must complete cleanly').toBe('completed')

  // ---------- 4) at least 5 google_maps leads inserted ----------
  const googleMapsAfter = await countLeadsBySource(db, 'google_maps')
  expect(googleMapsAfter - googleMapsBefore, 'expected ≥5 new google_maps leads').toBeGreaterThanOrEqual(TARGET_LEAD_COUNT)

  // Snapshot the 5 most-recently-inserted google_maps leads — the rest of
  // the spec operates on these. created_at presence is asserted defensively.
  const { data: newLeads, error: newLeadsErr } = await db
    .from('leads')
    .select('unique_key, name, company_name, website, audit_status, seo_score, lead_source, created_at')
    .eq('lead_source', 'google_maps')
    .order('created_at', { ascending: false })
    .limit(TARGET_LEAD_COUNT)
  if (newLeadsErr) throw newLeadsErr
  expect(newLeads?.length ?? 0).toBe(TARGET_LEAD_COUNT)
  const targetKeys = newLeads!.map((l) => l.unique_key as string)

  // ---------- 5) Audit All → wait for audit_status='Completed' on the 5 ----------
  await page.reload({ waitUntil: 'networkidle' })
  await page.getByRole('button', { name: 'Audit All' }).click()

  await pollUntil(
    async () => {
      const { data, error } = await db
        .from('leads')
        .select('unique_key, audit_status, seo_score')
        .in('unique_key', targetKeys)
      if (error) throw error
      if (!data || data.length < TARGET_LEAD_COUNT) return null
      const allDone = data.every((r) => String(r.audit_status || '').toLowerCase() === 'completed')
      return allDone ? data : null
    },
    AUDIT_POLL_TIMEOUT_MS,
    'all 5 target leads to reach audit_status=Completed',
  )

  // ---------- 6) seo_score set + 0..100 on each ----------
  const { data: audited, error: auditedErr } = await db
    .from('leads')
    .select('unique_key, audit_status, seo_score')
    .in('unique_key', targetKeys)
  if (auditedErr) throw auditedErr
  for (const row of audited || []) {
    expect(row.audit_status?.toLowerCase()).toBe('completed')
    expect(row.seo_score, `seo_score must be set on ${row.unique_key}`).not.toBeNull()
    const score = Number(row.seo_score)
    expect(Number.isFinite(score)).toBe(true)
    expect(score).toBeGreaterThanOrEqual(0)
    expect(score).toBeLessThanOrEqual(100)
  }

  // ---------- 7) Hunt All → social fields populated where present ----------
  await page.getByRole('button', { name: 'Hunt All' }).click()

  // Wait for the most recent hunt-shaped orchestration job to finish.
  const huntStartedAt = new Date().toISOString()
  await pollUntil(
    async () => {
      const { data, error } = await db
        .from('orchestration_jobs')
        .select('id, status, type, completed_at, started_at')
        .gte('started_at', huntStartedAt)
        .order('started_at', { ascending: false })
        .limit(1)
      if (error) throw error
      const row = data?.[0]
      if (row && (row.status === 'completed' || row.status === 'failed')) return row
      return null
    },
    HUNT_POLL_TIMEOUT_MS,
    'hunt orchestration job completion',
  )

  const { data: hunted, error: huntedErr } = await db
    .from('leads')
    .select('unique_key, facebook, instagram, linkedin, tiktok, pinterest, email, phone, website')
    .in('unique_key', targetKeys)
  if (huntedErr) throw huntedErr
  const SOCIAL = ['facebook', 'instagram', 'linkedin', 'tiktok', 'pinterest'] as const
  // Social populated WHERE PRESENT — a small Mostar business may have zero
  // socials. Soft assertion: at least one lead has at least one social link,
  // OR all leads with a website got *some* enrichment field populated.
  const anySocial = (hunted || []).some((l) => SOCIAL.some((k) => Boolean((l as Record<string, unknown>)[k])))
  const anyContact = (hunted || []).some((l) => l.email || l.phone)
  expect(anySocial || anyContact, 'hunt must populate some enrichment field on at least one lead').toBe(true)

  // ---------- 8) Draft Email → modal contains company_name ----------
  // Pick a lead that has a company_name we can match. Fall back to `name`.
  const draftSeedRow = await db
    .from('leads')
    .select('unique_key, name, company_name')
    .eq('unique_key', targetKeys[0])
    .maybeSingle()
  if (draftSeedRow.error) throw draftSeedRow.error
  const draftSeed = draftSeedRow.data as { unique_key: string; name: string | null; company_name: string | null } | null
  const personalisationToken = String(draftSeed?.company_name || draftSeed?.name || '').trim()
  expect(personalisationToken.length, 'need a non-empty company_name or name to grep for in the draft').toBeGreaterThan(0)

  // Click the Draft email button for that lead. aria-label format:
  // `Draft email outreach for ${company_name || name || 'lead'}`
  const draftButton = page.getByRole('button', { name: new RegExp(`^Draft email outreach for `) }).first()
  await draftButton.scrollIntoViewIfNeeded()
  await draftButton.click()

  const outreachModal = page.getByRole('dialog', { name: /Outreach for / })
  await expect(outreachModal).toBeVisible({ timeout: 60_000 })
  // The draft body is the only large pre-wrapped block in the modal.
  const bodyText = (await outreachModal.innerText()).trim()
  expect(bodyText.length, 'outreach draft must be non-empty').toBeGreaterThan(40)
  // Personalisation: company name (or name fallback) must appear somewhere
  // in the modal (heading is "Outreach for {leadName}" plus body content).
  expect(bodyText.toLowerCase()).toContain(personalisationToken.toLowerCase())

  // Close the modal so it doesn't intercept the next nav.
  await page.getByRole('button', { name: 'Close outreach draft' }).click()
  await expect(outreachModal).toBeHidden()

  // ---------- 9) Campaign → generate → export CSV ----------
  await page.goto('/campaigns')
  await page.getByRole('button', { name: /New Campaign/ }).first().click()
  const campaignName = `e2e-${Date.now()}`
  await page.fill('#campaign-name', campaignName)
  await page.selectOption('#campaign-channel', 'email')
  await page.getByRole('button', { name: 'Create Campaign' }).click()

  // Open the campaign detail view (the new one).
  await page.getByText(campaignName, { exact: true }).first().click()
  await page.getByRole('button', { name: 'Generate Messages' }).click()

  // Wait for campaign_messages rows. Look up campaign by name to get id.
  const { data: campRows, error: campErr } = await db
    .from('campaigns')
    .select('id, name')
    .eq('name', campaignName)
    .maybeSingle()
  if (campErr) throw campErr
  expect(campRows?.id, 'campaign row must exist').toBeTruthy()
  const campaignId = campRows!.id as string

  await pollUntil(
    async () => {
      const { count, error } = await db
        .from('campaign_messages')
        .select('*', { count: 'exact', head: true })
        .eq('campaign_id', campaignId)
      if (error) throw error
      return (count ?? 0) > 0 ? count : null
    },
    5 * 60_000,
    'campaign_messages to be generated',
  )

  // Click Export CSV — anchor with download attribute fires a download event.
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 30_000 }),
    page.getByRole('button', { name: 'Export CSV' }).click(),
  ])
  const filename = download.suggestedFilename()
  expect(filename).toMatch(/^campaign-[a-f0-9]{8}-\d{4}-\d{2}-\d{2}\.csv$/)
  const tmpPath = await download.path()
  expect(tmpPath, 'download must resolve to a file path').toBeTruthy()
  const fs = await import('node:fs/promises')
  const csv = await fs.readFile(tmpPath!, 'utf8')
  // Structural checks — header row + at least one data row + RFC4180-ish
  // line shape. Don't pin exact columns (backend may evolve them) but
  // require some plausible outreach-shaped columns.
  const lines = csv.split(/\r?\n/).filter((l) => l.length > 0)
  expect(lines.length, 'CSV needs header + ≥1 row').toBeGreaterThanOrEqual(2)
  const header = lines[0].toLowerCase()
  expect(header.split(',').length, 'CSV header must have ≥2 columns').toBeGreaterThanOrEqual(2)
  // CSV-injection guard pin: no data cell may start with =, +, -, @, \t, \r
  // (sanitize_dataframe_for_csv prefixes those with `'`). Sample a few cells.
  for (const line of lines.slice(1, Math.min(lines.length, 6))) {
    const firstCell = line.replace(/^"/, '')
    expect(/^[=+\-@\t\r]/.test(firstCell), `CSV cell must not start with formula trigger: ${firstCell.slice(0, 40)}`).toBe(false)
  }

  // ---------- 10) DELETE /leads/clear — both X-API-Key AND X-Admin-Token required ----------
  // Hit the backend DIRECTLY (bypassing the Next proxy that injects both)
  // so we can prove the header-pair invariant.
  const hdrCommon = { 'Content-Type': 'application/json' }

  // 10a) No headers → 403 (missing API key — verify_api_key rejects).
  const noHdr = await fetch(`${BACKEND_URL}/leads/clear`, { method: 'DELETE', headers: hdrCommon })
  expect(noHdr.status, 'no creds must be rejected').toBe(403)

  // 10b) API key only → 403 (X-Admin-Token missing).
  const keyOnly = await fetch(`${BACKEND_URL}/leads/clear`, {
    method: 'DELETE',
    headers: { ...hdrCommon, 'X-API-Key': API_KEY },
  })
  expect(keyOnly.status, 'API key alone must not suffice for destructive route').toBe(403)

  // 10c) Admin token only → 403 (X-API-Key missing → verify_api_key rejects first).
  const adminOnly = await fetch(`${BACKEND_URL}/leads/clear`, {
    method: 'DELETE',
    headers: { ...hdrCommon, 'X-Admin-Token': ADMIN_TOKEN },
  })
  expect(adminOnly.status, 'admin token alone must not suffice').toBe(403)

  // 10d) Both → success (200/204).
  const both = await fetch(`${BACKEND_URL}/leads/clear`, {
    method: 'DELETE',
    headers: { ...hdrCommon, 'X-API-Key': API_KEY, 'X-Admin-Token': ADMIN_TOKEN },
  })
  expect([200, 204]).toContain(both.status)

  // 10e) Leads table is empty.
  const { count: remaining, error: remErr } = await db
    .from('leads')
    .select('*', { count: 'exact', head: true })
  if (remErr) throw remErr
  expect(remaining ?? 0).toBe(0)
})
