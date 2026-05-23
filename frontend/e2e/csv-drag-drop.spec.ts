import { test, expect, type Page, type JSHandle } from '@playwright/test'
import { createClient, type SupabaseClient } from '@supabase/supabase-js'

// Drag-and-drop ingest. Exercises the drop-zone handlers wired onto
// dashboard-container in frontend/app/page.tsx. Playwright can't construct a
// File in Node and pass it across the protocol, so each test builds a
// DataTransfer inside the page via evaluateHandle and dispatches the
// drag/drop events with that handle attached.
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
test.describe.configure({ mode: 'serial' })

const NONCE = Date.now().toString(36)
const FIXTURE_DOMAIN = `e2e-dnd-${NONCE}.test`
const INGEST_TIMEOUT_MS = 60_000
const INGEST_POLL_INTERVAL_MS = 1_500
const ROOT = '[data-testid="dashboard-root"]'
const OVERLAY = '[data-testid="drop-overlay"]'

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
  await page.goto('/')
}

function csvBody(label: string, count: number): string {
  const header = 'name,website,email,phone,address'
  const rows: string[] = [header]
  for (let i = 0; i < count; i++) {
    rows.push(`${label} Co ${i.toString().padStart(2, '0')},https://example.com/${label}/${i},lead${i}@${FIXTURE_DOMAIN},+1-555-${1000 + i},${i} Test St`)
  }
  return rows.join('\n') + '\n'
}

type FilePayload = { name: string; type: string; content: string }

async function buildDataTransfer(
  page: Page,
  files: FilePayload[],
): Promise<JSHandle<DataTransfer>> {
  return page.evaluateHandle((items) => {
    const dt = new DataTransfer()
    for (const f of items) {
      dt.items.add(new File([f.content], f.name, { type: f.type }))
    }
    return dt
  }, files)
}

async function dispatchDrop(page: Page, files: FilePayload[]) {
  const dt = await buildDataTransfer(page, files)
  await page.dispatchEvent(ROOT, 'dragenter', { dataTransfer: dt })
  await page.dispatchEvent(ROOT, 'dragover', { dataTransfer: dt })
  await page.dispatchEvent(ROOT, 'drop', { dataTransfer: dt })
  await dt.dispose()
}

async function dispatchDragEnter(page: Page, files: FilePayload[]): Promise<JSHandle<DataTransfer>> {
  const dt = await buildDataTransfer(page, files)
  await page.dispatchEvent(ROOT, 'dragenter', { dataTransfer: dt })
  await page.dispatchEvent(ROOT, 'dragover', { dataTransfer: dt })
  return dt
}

async function dispatchDragLeave(page: Page, dt: JSHandle<DataTransfer>) {
  await page.dispatchEvent(ROOT, 'dragleave', { dataTransfer: dt })
  await dt.dispose()
}

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
  throw new Error(`drop ingest never reached ${expected} fixture leads`)
}

async function purgeFixtureLeads(db: SupabaseClient): Promise<void> {
  await db.from('leads').delete().like('email', `%@${FIXTURE_DOMAIN}`)
}

async function readLastToast(page: Page, expectText: RegExp, timeout = 4_000): Promise<void> {
  // Toasts auto-dismiss in 3.5s — use Playwright's auto-waiting locator
  // rather than a snapshot to avoid the race.
  const toast = page.locator('.toast').filter({ hasText: expectText })
  await expect(toast).toBeVisible({ timeout })
}

test.afterAll(async () => {
  const db = admin()
  try {
    await db.from('leads').delete().like('email', `%@${FIXTURE_DOMAIN}`)
  } catch {
    /* best-effort */
  }
})

test.describe('CSV drag-and-drop', () => {
  test('single CSV drop ingests rows', async ({ page }) => {
    const db = admin()
    await purgeFixtureLeads(db)
    await login(page)

    await dispatchDrop(page, [
      { name: 'leads-dnd.csv', type: 'text/csv', content: csvBody('dnd-single', 5) },
    ])
    await readLastToast(page, /processing in the background|imported/i)
    await waitForLeadCount(db, 5)
  })

  test('multi-file drop: first accepted, others rejected with a message', async ({ page }) => {
    const db = admin()
    await purgeFixtureLeads(db)
    await login(page)

    await dispatchDrop(page, [
      { name: 'first.csv', type: 'text/csv', content: csvBody('dnd-first', 3) },
      { name: 'second.csv', type: 'text/csv', content: csvBody('dnd-second', 99) },
      { name: 'third.csv', type: 'text/csv', content: csvBody('dnd-third', 99) },
    ])
    // First is taken — toast names the ignored count.
    await readLastToast(page, /Only the first file was imported.*2 other/i)
    await waitForLeadCount(db, 3)

    // Defensively confirm the others did NOT land.
    const { count } = await db
      .from('leads')
      .select('*', { count: 'exact', head: true })
      .like('email', `%@${FIXTURE_DOMAIN}`)
    expect(count ?? 0, 'only first file (3 leads) must be in DB').toBe(3)
  })

  test('non-CSV (.pdf, .png) drop is rejected with a clear message', async ({ page }) => {
    const db = admin()
    await purgeFixtureLeads(db)
    await login(page)

    // PDF
    await dispatchDrop(page, [
      { name: 'invoice.pdf', type: 'application/pdf', content: '%PDF-1.4 fake' },
    ])
    await readLastToast(page, /Only CSV files are accepted/i)

    // PNG
    await dispatchDrop(page, [
      { name: 'logo.png', type: 'image/png', content: '\x89PNG\r\n\x1a\n' },
    ])
    await readLastToast(page, /Only CSV files are accepted/i)

    // No rows landed.
    const { count } = await db
      .from('leads')
      .select('*', { count: 'exact', head: true })
      .like('email', `%@${FIXTURE_DOMAIN}`)
    expect(count ?? 0).toBe(0)
  })

  test('drop while another upload is pending is rejected', async ({ page }) => {
    const db = admin()
    await purgeFixtureLeads(db)
    await login(page)

    // Stall the first upload at the proxy so loading=true stays true long
    // enough for the second drop to see it. Without page.route there's no
    // reliable way to keep the in-flight window open — /upload returns
    // ~instantly because ingestion runs in a background task.
    let stalledOnce = false
    await page.route('**/api/proxy/upload', async (route) => {
      if (!stalledOnce) {
        stalledOnce = true
        await new Promise((r) => setTimeout(r, 3_000))
      }
      await route.continue()
    })

    // First drop (will stall ~3s at the proxy).
    const firstDrop = dispatchDrop(page, [
      { name: 'first.csv', type: 'text/csv', content: csvBody('dnd-pend-1', 2) },
    ])

    // Give the first drop a moment to flip loading=true.
    await page.waitForTimeout(250)

    // Second drop while the first is in flight → must be rejected, no ingest.
    await dispatchDrop(page, [
      { name: 'second.csv', type: 'text/csv', content: csvBody('dnd-pend-2', 7) },
    ])
    await readLastToast(page, /Upload already in progress/i)

    // Let the first drop finish.
    await firstDrop
    await page.unroute('**/api/proxy/upload')

    // Only the first file's 2 leads should have landed.
    await waitForLeadCount(db, 2)
    const { count } = await db
      .from('leads')
      .select('*', { count: 'exact', head: true })
      .like('email', `%@${FIXTURE_DOMAIN}`)
    expect(count ?? 0, 'queued/rejected second drop must not have ingested').toBe(2)
  })

  test('drag-leave restores normal UI (no stuck overlay)', async ({ page }) => {
    await login(page)
    await expect(page.locator(OVERLAY)).toHaveCount(0)

    // Drag-enter → overlay visible.
    const dt = await dispatchDragEnter(page, [
      { name: 'hover.csv', type: 'text/csv', content: csvBody('dnd-hover', 1) },
    ])
    await expect(page.locator(OVERLAY)).toBeVisible()

    // Drag-leave → overlay gone, no leftover handler state. The ref-counter
    // in onDashboardDragEnter/Leave is what makes this not flicker; if it
    // ever regresses, the overlay will stay visible after dragleave fires.
    await dispatchDragLeave(page, dt)
    await expect(page.locator(OVERLAY)).toHaveCount(0)

    // And a subsequent normal navigation still works — nothing is intercepted.
    await page.goto('/insights')
    await expect(page).toHaveURL(/\/insights/)
  })
})
