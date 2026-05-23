import { test, expect, type Page } from '@playwright/test'
import { createClient, type SupabaseClient } from '@supabase/supabase-js'
import { mkdtempSync, writeFileSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// CSV-upload E2E. Five fixtures exercise the ingest pipeline + its limits:
//   1. canonical            — 10 well-formed rows
//   2. messy-headers        — 10 rows with non-canonical column names
//                             (exercises GeminiMapper AI column mapping)
//   3. utf8-bom             — 10 rows prefixed with a UTF-8 BOM
//   4. 50mb                 — 50 MB + 1 byte CSV; must 413 at the proxy
//                             (bypasses the client's 10 MB gate)
//   5. formula-injection    — 10 rows whose `name` cell starts with
//                             `=cmd|...`; assert sanitisation in the DB
//
// Required env:
//   E2E_BASE_URL E2E_EMAIL E2E_PASSWORD
//   E2E_SUPABASE_URL E2E_SUPABASE_SERVICE_ROLE_KEY

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
const SUPABASE_URL = process.env.E2E_SUPABASE_URL || ''
const SUPABASE_SERVICE_ROLE_KEY = process.env.E2E_SUPABASE_SERVICE_ROLE_KEY || ''

const MISSING = (
  [
    ['E2E_EMAIL', EMAIL],
    ['E2E_PASSWORD', PASSWORD],
    ['E2E_SUPABASE_URL', SUPABASE_URL],
    ['E2E_SUPABASE_SERVICE_ROLE_KEY', SUPABASE_SERVICE_ROLE_KEY],
  ] as const
)
  .filter(([, v]) => !v)
  .map(([k]) => k)

test.skip(MISSING.length > 0, `Missing env: ${MISSING.join(', ')}`)
test.describe.configure({ mode: 'serial' })

const NONCE = Date.now().toString(36)
const FIXTURE_DOMAIN = `e2e-csv-${NONCE}.test`
const INGEST_TIMEOUT_MS = 90_000
const INGEST_POLL_INTERVAL_MS = 2_000

const FIXTURE_DIR = mkdtempSync(join(tmpdir(), 'csv-upload-e2e-'))
const fixtures = {
  canonical: join(FIXTURE_DIR, 'leads-canonical.csv'),
  messy: join(FIXTURE_DIR, 'leads-messy-headers.csv'),
  bom: join(FIXTURE_DIR, 'leads-utf8-bom.csv'),
  big: join(FIXTURE_DIR, 'leads-50mb.csv'),
  formula: join(FIXTURE_DIR, 'leads-formula-injection.csv'),
}

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

function buildRows(label: string, count: number): string[] {
  // Email domain marks fixtures so we can delete + count them cleanly.
  const rows: string[] = []
  for (let i = 0; i < count; i++) {
    rows.push([
      `${label} Co ${i.toString().padStart(2, '0')}`,
      `https://example.com/${label}/${i}`,
      `lead${i}@${FIXTURE_DOMAIN}`,
      `+1-555-${(1000 + i).toString()}`,
      `${i} Test St, Mostar`,
    ].join(','))
  }
  return rows
}

function writeCanonical(): void {
  const header = 'name,website,email,phone,address'
  writeFileSync(fixtures.canonical, [header, ...buildRows('canonical', 10)].join('\n') + '\n')
}

function writeMessy(): void {
  // Headers a Gemini mapper has to resolve to canonical names.
  const header = 'Biz Name,Web URL,Contact Email,Tel,Street'
  writeFileSync(fixtures.messy, [header, ...buildRows('messy', 10)].join('\n') + '\n')
}

function writeUtf8Bom(): void {
  const header = 'name,website,email,phone,address'
  const body = [header, ...buildRows('bom', 10)].join('\n') + '\n'
  writeFileSync(fixtures.bom, '﻿' + body)
}

function writeFiftyMb(): void {
  const targetBytes = 50 * 1024 * 1024 + 1 // 1 byte past the proxy ceiling
  const header = 'name,website,email,phone,address\n'
  const filler = 'a,b,c,d,' + 'x'.repeat(256) + '\n'
  // Pre-compute how many filler rows fit, then add one more to overshoot.
  const headerBytes = Buffer.byteLength(header)
  const rowBytes = Buffer.byteLength(filler)
  const rowsNeeded = Math.ceil((targetBytes - headerBytes) / rowBytes)
  const buffers: Buffer[] = [Buffer.from(header)]
  for (let i = 0; i < rowsNeeded; i++) buffers.push(Buffer.from(filler))
  writeFileSync(fixtures.big, Buffer.concat(buffers))
  const actual = statSync(fixtures.big).size
  if (actual <= 50 * 1024 * 1024) {
    throw new Error(`fixture leads-50mb.csv is ${actual} bytes; must exceed 50 MB to test the proxy 413 boundary`)
  }
}

function writeFormulaInjection(): void {
  // Each row's `name` cell starts with a formula-trigger character.
  // sanitize_dataframe_for_csv prefixes those with `'` on export, but the
  // DB-level contract is: the raw string never survives to a cell that
  // Excel/Sheets would execute. We assert that contract from the DB.
  const header = 'name,website,email,phone,address'
  const rows: string[] = []
  const triggers = ['=cmd|', '=HYPERLINK(', '+1+', '-1+1', '@SUM(', '\tTAB', '\rCR', '=2+5', '=1+1', '=BAD(']
  for (let i = 0; i < triggers.length; i++) {
    rows.push([
      `"${triggers[i]}A1)" pwn`,
      `https://example.com/formula/${i}`,
      `lead${i}@${FIXTURE_DOMAIN}`,
      `+1-555-${(2000 + i).toString()}`,
      `${i} Test St, Mostar`,
    ].join(','))
  }
  writeFileSync(fixtures.formula, [header, ...rows].join('\n') + '\n')
}

test.beforeAll(() => {
  writeCanonical()
  writeMessy()
  writeUtf8Bom()
  writeFiftyMb()
  writeFormulaInjection()
})

test.afterAll(async () => {
  // Best-effort cleanup of all fixture-namespaced rows.
  const db = admin()
  try {
    await db.from('leads').delete().like('email', `%@${FIXTURE_DOMAIN}`)
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn('csv-upload cleanup failed:', err)
  }
})

async function waitForLeadCount(db: SupabaseClient, expected: number): Promise<void> {
  const deadline = Date.now() + INGEST_TIMEOUT_MS
  while (Date.now() < deadline) {
    const { count, error } = await db
      .from('leads')
      .select('*', { count: 'exact', head: true })
      .like('email', `%@${FIXTURE_DOMAIN}`)
    if (error) throw error
    if ((count ?? 0) >= expected) return
    await new Promise((r) => setTimeout(r, INGEST_POLL_INTERVAL_MS))
  }
  throw new Error(`ingest never reached ${expected} fixture leads within ${INGEST_TIMEOUT_MS}ms`)
}

async function purgeFixtureLeads(db: SupabaseClient): Promise<void> {
  // Drop fixture rows between sub-tests so each starts from zero. Uses the
  // unique e2e fixture domain set in FIXTURE_DOMAIN — won't touch operator data.
  await db.from('leads').delete().like('email', `%@${FIXTURE_DOMAIN}`)
}

async function uploadViaUi(page: Page, filePath: string): Promise<void> {
  // The dashboard exposes the file input as a hidden #csv-upload. setInputFiles
  // on the hidden input bypasses the "Import CSV" button click but produces the
  // identical change event the button would.
  await page.goto('/')
  await page.setInputFiles('#csv-upload', filePath)
}

test.describe('CSV upload', () => {
  test('canonical 10-row CSV ingests 10 leads', async ({ page }) => {
    const db = admin()
    await purgeFixtureLeads(db)
    await login(page)
    await uploadViaUi(page, fixtures.canonical)
    await waitForLeadCount(db, 10)
  })

  test('messy-headers CSV maps non-canonical columns via AI mapper', async ({ page }) => {
    const db = admin()
    await purgeFixtureLeads(db)
    await login(page)
    await uploadViaUi(page, fixtures.messy)
    await waitForLeadCount(db, 10)

    // After AI remap, the rows should land with populated canonical columns.
    // We don't pin every column (Gemini may legitimately leave some blank),
    // but at least name + email + website must land on most rows.
    const { data, error } = await db
      .from('leads')
      .select('name, email, website')
      .like('email', `%@${FIXTURE_DOMAIN}`)
    if (error) throw error
    const withCore = (data || []).filter((r) => r.name && r.email && r.website)
    expect(withCore.length, `messy-header rows mapped with name+email+website: ${withCore.length}`).toBeGreaterThanOrEqual(8)
  })

  test('UTF-8 BOM is stripped and headers parse', async ({ page }) => {
    const db = admin()
    await purgeFixtureLeads(db)
    await login(page)
    await uploadViaUi(page, fixtures.bom)
    await waitForLeadCount(db, 10)

    // BOM contamination would land as a column named "﻿name" — the row
    // would then have a NULL name. Assert names parsed cleanly.
    const { data, error } = await db
      .from('leads')
      .select('name')
      .like('email', `%@${FIXTURE_DOMAIN}`)
    if (error) throw error
    expect((data || []).every((r) => typeof r.name === 'string' && r.name.length > 0)).toBe(true)
  })

  test('50 MB + 1 byte CSV is rejected with 413 at the proxy', async ({ page }) => {
    // Bypass the client-side 10 MB gate by POSTing directly to the same-origin
    // /api/proxy/upload route. APIRequestContext shares the page's storage
    // state (Supabase session cookies) so the proxy's auth check passes;
    // the proxy's MAX_PROXY_BODY_BYTES then trips and returns 413.
    await login(page)
    const body = await import('node:fs/promises').then((m) => m.readFile(fixtures.big))
    const resp = await page.context().request.post('/api/proxy/upload', {
      multipart: {
        file: {
          name: 'leads-50mb.csv',
          mimeType: 'text/csv',
          buffer: body,
        },
      },
    })
    expect(
      resp.status(),
      `oversized upload must 413, got ${resp.status()} body=${await resp.text().catch(() => '')}`,
    ).toBe(413)
  })

  test('formula-injection CSV is sanitised before reaching the DB', async ({ page }) => {
    const db = admin()
    await purgeFixtureLeads(db)
    await login(page)
    await uploadViaUi(page, fixtures.formula)
    await waitForLeadCount(db, 10)

    const { data, error } = await db
      .from('leads')
      .select('name')
      .like('email', `%@${FIXTURE_DOMAIN}`)
    if (error) throw error

    // Contract: no string cell may start with a formula trigger character
    // (=, +, -, @, \t, \r). The export-side guard
    // (sanitize_dataframe_for_csv) prefixes those with `'` so a downloaded
    // CSV is safe — but anything that ingests and round-trips through the
    // DB risks ending up in another tool that doesn't sanitise. This test
    // pins the contract at the DB layer.
    const offenders = (data || []).filter((r) => {
      const v = (r.name ?? '') as string
      return /^[=+\-@\t\r]/.test(v)
    })
    expect(
      offenders,
      `DB rows whose name still starts with a formula-trigger char (sanitisation missing):\n${offenders.map((o) => `  ${JSON.stringify(o.name)}`).join('\n')}`,
    ).toEqual([])
  })
})
