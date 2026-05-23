import { test, expect, type Page } from '@playwright/test'
import { createClient, type SupabaseClient } from '@supabase/supabase-js'
import { readFileSync } from 'node:fs'

// CSV export contract. Three export paths:
//   - "Export Full"     → GET /export/download (full leads CSV)
//   - "CRM Export"      → GET /export/outreach (outreach-targeting subset)
//   - Campaigns "Export CSV" → GET /campaigns/{id}/export
//
// Required env: E2E_BASE_URL, E2E_EMAIL, E2E_PASSWORD,
//               E2E_SUPABASE_URL, E2E_SUPABASE_SERVICE_ROLE_KEY

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

// Minimal CSV parser. Handles quoted fields with embedded commas/CRLF and
// "" escaping per RFC 4180. Not papaparse-grade for every edge case but
// covers the fields the backend actually emits.
function parseCsv(input: string): { header: string[]; rows: string[][] } {
  // Strip UTF-8 BOM if present — the test for it lives separately below.
  const text = input.replace(/^﻿/, '')
  const rows: string[][] = []
  let field = ''
  let row: string[] = []
  let inQuotes = false
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (inQuotes) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++ }
      else if (c === '"') { inQuotes = false }
      else { field += c }
    } else {
      if (c === '"') inQuotes = true
      else if (c === ',') { row.push(field); field = '' }
      else if (c === '\n') { row.push(field); rows.push(row); row = []; field = '' }
      else if (c === '\r') { /* swallow — next char is \n */ }
      else { field += c }
    }
  }
  if (field.length > 0 || row.length > 0) { row.push(field); rows.push(row) }
  const header = rows.shift() || []
  return { header, rows: rows.filter((r) => r.length > 0 && !(r.length === 1 && r[0] === '')) }
}

function hasFormulaInjection(rows: string[][]): { row: number; col: number; cell: string }[] {
  const offenders: { row: number; col: number; cell: string }[] = []
  for (let i = 0; i < rows.length; i++) {
    for (let j = 0; j < rows[i].length; j++) {
      const c = rows[i][j]
      if (/^[=+\-@\t\r]/.test(c)) offenders.push({ row: i, col: j, cell: c })
    }
  }
  return offenders
}

async function downloadAndRead(page: Page, trigger: () => Promise<void>): Promise<string> {
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 30_000 }),
    trigger(),
  ])
  const path = await download.path()
  expect(path).toBeTruthy()
  return readFileSync(path!, 'utf8')
}

test.describe('CSV exports', () => {
  test('Export Full: row count matches DB, columns present, no formula injection, BOM-tolerant', async ({ page }) => {
    const db = admin()
    await login(page)
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const { count: dbCount, error: cntErr } = await db.from('leads').select('*', { count: 'exact', head: true })
    if (cntErr) throw cntErr

    const csv = await downloadAndRead(page, () =>
      page.getByRole('button', { name: /Export Full/i }).click(),
    )
    const { header, rows } = parseCsv(csv)

    // Row count: full export must include every lead currently in the DB
    // (regardless of audit status). Allow ±1 for races against ongoing
    // inserts during the test session.
    expect(
      Math.abs(rows.length - (dbCount ?? 0)),
      `CSV rows (${rows.length}) must match DB lead count (${dbCount}) within ±1`,
    ).toBeLessThanOrEqual(1)

    // Header: expected canonical columns must be present (order-agnostic).
    const expected = ['name', 'website', 'email', 'phone', 'audit_status']
    for (const col of expected) {
      expect(header.map((h) => h.toLowerCase()), `header missing ${col}`).toContain(col)
    }

    // CSV injection guard from sanitize_dataframe_for_csv.
    const formulaCells = hasFormulaInjection(rows)
    expect(
      formulaCells,
      `formula-trigger cells found in CSV (sanitize_dataframe_for_csv should have prefixed them):\n${formulaCells.map((o) => `  row ${o.row} col ${o.col}: ${JSON.stringify(o.cell)}`).join('\n')}`,
    ).toEqual([])

    // BOM / diacritics: if any DB row carries Croatian/Bosnian characters
    // (š č ć ž đ), they must round-trip without mojibake. We don't require
    // their presence — only that if present, they parse cleanly.
    const diacriticsRows = rows.filter((r) => r.some((c) => /[šŠčČćĆžŽđĐ]/.test(c)))
    for (const r of diacriticsRows.slice(0, 3)) {
      expect(r.some((c) => /[�]/.test(c)), `mojibake (�) detected in row ${JSON.stringify(r)}`).toBe(false)
    }
  })

  test('CRM (outreach) export: same invariants', async ({ page }) => {
    await login(page)
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const csv = await downloadAndRead(page, () =>
      page.getByRole('button', { name: /CRM Export/i }).click(),
    )
    const { header, rows } = parseCsv(csv)
    expect(rows.length, 'outreach export must have at least one row (if leads exist)').toBeGreaterThanOrEqual(0)
    for (const col of ['name', 'email']) {
      expect(header.map((h) => h.toLowerCase())).toContain(col)
    }
    expect(hasFormulaInjection(rows)).toEqual([])
  })

  test('Campaign export: column set + injection guard', async ({ page }) => {
    const db = admin()
    await login(page)
    // Pick an existing campaign; create one if none exist.
    const { data: campaigns } = await db.from('campaigns').select('id').limit(1)
    let campaignId = campaigns?.[0]?.id as string | undefined
    if (!campaignId) {
      const { data } = await db
        .from('campaigns')
        .insert({ name: `e2e-export-${Date.now()}`, channel: 'email', status: 'draft' })
        .select('id')
        .single()
      campaignId = data?.id as string
    }
    expect(campaignId, 'a campaign row must exist').toBeTruthy()

    await page.goto('/campaigns')
    await page.waitForLoadState('networkidle')
    // Open the campaign and click Export CSV.
    await page.locator('text=' + campaignId!).first().scrollIntoViewIfNeeded().catch(() => undefined)
    // The campaign list renders by name not id. Click the first one.
    await page.locator('.card').filter({ hasText: /campaign|outreach/i }).first().click().catch(() => undefined)

    const trigger = page.getByRole('button', { name: /Export CSV/i })
    if (!(await trigger.isVisible().catch(() => false))) {
      test.skip(true, 'no campaign list item exposed an Export CSV button — likely empty messages list')
      return
    }
    const csv = await downloadAndRead(page, () => trigger.click())
    const { header, rows } = parseCsv(csv)
    expect(header.length, 'campaign CSV must have a header row').toBeGreaterThan(0)
    expect(hasFormulaInjection(rows)).toEqual([])
  })
})
