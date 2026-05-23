import { test, expect, type Page } from '@playwright/test'
import { createClient, type SupabaseClient } from '@supabase/supabase-js'

// FilterBar + sort + URL-state contract. Seeds 250 leads with diverse
// audit_status / seo_score / segment values via service-role, then drives
// the dashboard's filter UI and asserts the contract.
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
const FIXTURE_DOMAIN = `e2e-filter-${NONCE}.test`
const TARGET_SEGMENT = 'Performance Optimization'
const SEGMENTS = [
  'Performance Optimization',
  'Low Priority Prospect',
  'High Conversion Target',
  'Tech Stack Modernization',
  'Manual Review',
] as const
const STATUSES = ['Completed', 'Pending', 'Failed'] as const
const SEED_COUNT = 250

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

async function seed250(db: SupabaseClient): Promise<void> {
  const rows = Array.from({ length: SEED_COUNT }, (_, i) => {
    const segment = SEGMENTS[i % SEGMENTS.length]
    const status = STATUSES[i % STATUSES.length]
    const seo = status === 'Completed' ? (i * 13) % 101 : null
    return {
      unique_key: `${NONCE}-${i.toString().padStart(3, '0')}`,
      name: `Fixture ${i.toString().padStart(3, '0')}`,
      company_name: `Fixture Co ${i.toString().padStart(3, '0')}`,
      website: `https://example.com/fixture/${i}`,
      email: `lead${i}@${FIXTURE_DOMAIN}`,
      audit_status: status,
      seo_score: seo,
      audit_results: seo == null ? null : { score: seo },
      outreach_score: status === 'Completed' ? ((i * 7) % 101) : null,
      segment,
      lead_source: 'e2e_fixture',
    }
  })
  // PostgREST default upsert limit is generous but we batch defensively.
  for (let chunk = 0; chunk < rows.length; chunk += 50) {
    const slice = rows.slice(chunk, chunk + 50)
    const { error } = await db.from('leads').upsert(slice, { onConflict: 'unique_key' })
    if (error) throw error
  }
}

async function cleanup(db: SupabaseClient): Promise<void> {
  await db.from('leads').delete().like('email', `%@${FIXTURE_DOMAIN}`)
}

async function getTableState(page: Page): Promise<{ segments: string[]; seoScores: (number | null)[]; total: number }> {
  // Scope to fixture rows so pre-existing leads in the operator's DB
  // don't contaminate the assertion. The fixture unique_key carries the
  // run NONCE, set at module load. data-segment + data-seo-score are
  // attached on each <tr> in page.tsx — rendered ground truth.
  return page.evaluate((nonce) => {
    const rows = Array.from(document.querySelectorAll('tbody tr.table-row-hover')) as HTMLTableRowElement[]
    const fixtureRows = rows.filter((tr) => (tr.getAttribute('data-unique-key') || '').startsWith(nonce + '-'))
    const segments: string[] = []
    const seoScores: (number | null)[] = []
    for (const tr of fixtureRows) {
      segments.push(tr.getAttribute('data-segment') || '')
      const raw = tr.getAttribute('data-seo-score') || ''
      const n = Number.parseInt(raw, 10)
      seoScores.push(Number.isFinite(n) ? n : null)
    }
    return { segments, seoScores, total: fixtureRows.length }
  }, NONCE)
}

test.beforeAll(async () => {
  const db = admin()
  await cleanup(db) // in case a previous run died mid-test
  await seed250(db)
})

test.afterAll(async () => {
  const db = admin()
  await cleanup(db)
})

test.describe('FilterBar + sort + URL params', () => {
  test('filter by segment shows only matching rows', async ({ page }) => {
    await login(page)
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    // The full inventory paginates at 50; that's fine — we just need to
    // assert that *every visible row* matches the chosen segment.
    await page.selectOption('#filter-segment', TARGET_SEGMENT)
    await page.waitForTimeout(300) // let memo + render settle

    const state = await getTableState(page)
    expect(state.total, 'segment filter must show at least one row').toBeGreaterThan(0)
    const nonMatching = state.segments.filter((s) => s !== TARGET_SEGMENT)
    expect(
      nonMatching,
      `${nonMatching.length} visible rows have a segment != "${TARGET_SEGMENT}"`,
    ).toEqual([])
  })

  test('sort by seo_score desc: first row >= last row', async ({ page }) => {
    await login(page)
    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await page.selectOption('#filter-audit-status', 'Completed') // restrict to scored rows
    await page.selectOption('#sort-leads', 'seo_score_desc')
    await page.waitForTimeout(300)

    const { seoScores } = await getTableState(page)
    const numericScores = seoScores.filter((s): s is number => s != null)
    expect(numericScores.length, 'must have at least 2 scored rows for ordering').toBeGreaterThanOrEqual(2)
    expect(
      numericScores[0],
      `first row seo_score (${numericScores[0]}) must be >= last row (${numericScores[numericScores.length - 1]})`,
    ).toBeGreaterThanOrEqual(numericScores[numericScores.length - 1])
    // Stronger check: monotonic non-increasing.
    for (let i = 1; i < numericScores.length; i++) {
      expect(numericScores[i - 1], `row ${i - 1}=${numericScores[i - 1]} must be >= row ${i}=${numericScores[i]}`).toBeGreaterThanOrEqual(numericScores[i])
    }
  })

  test('filter + sort combine correctly', async ({ page }) => {
    await login(page)
    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await page.selectOption('#filter-segment', TARGET_SEGMENT)
    await page.selectOption('#filter-audit-status', 'Completed')
    await page.selectOption('#sort-leads', 'seo_score_desc')
    await page.waitForTimeout(300)

    const { segments, seoScores } = await getTableState(page)
    expect(segments.length, 'combined filter must yield rows').toBeGreaterThan(0)
    // All rows match the segment.
    expect(segments.every((s) => s === TARGET_SEGMENT)).toBe(true)
    // Scores monotonic non-increasing.
    const nums = seoScores.filter((s): s is number => s != null)
    for (let i = 1; i < nums.length; i++) {
      expect(nums[i - 1]).toBeGreaterThanOrEqual(nums[i])
    }
  })

  test('clear filters restores the unfiltered view', async ({ page }) => {
    await login(page)
    await page.goto('/')
    await page.waitForLoadState('networkidle')
    const baselineState = await getTableState(page)
    expect(baselineState.total).toBeGreaterThan(0)

    await page.selectOption('#filter-segment', TARGET_SEGMENT)
    await page.selectOption('#filter-audit-status', 'Completed')
    await page.waitForTimeout(300)
    const filteredState = await getTableState(page)
    expect(filteredState.total).toBeLessThan(baselineState.total)

    // Clear-filters button appears once a filter is active.
    await page.click('#clear-filters')
    await page.waitForTimeout(300)
    const restoredState = await getTableState(page)
    expect(restoredState.total, 'row count after clear must match baseline').toBe(baselineState.total)
  })

  test('URL params reflect filter state (shareable filtered view)', async ({ page }) => {
    await login(page)
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.selectOption('#filter-segment', TARGET_SEGMENT)
    await page.selectOption('#sort-leads', 'seo_score_desc')
    await page.waitForTimeout(400)

    // URL has segment=… and sort=… params.
    const u = new URL(page.url())
    expect(u.searchParams.get('segment')).toBe(TARGET_SEGMENT)
    expect(u.searchParams.get('sort')).toBe('seo_score_desc')

    // Shareable: opening the URL fresh hydrates the same state.
    const ctx = page.context()
    const tab2 = await ctx.newPage()
    await tab2.goto(u.pathname + u.search)
    await tab2.waitForLoadState('networkidle')
    // Hydration runs in the mount effect; give it a tick.
    await tab2.waitForTimeout(400)
    await expect(tab2.locator('#filter-segment')).toHaveValue(TARGET_SEGMENT)
    await expect(tab2.locator('#sort-leads')).toHaveValue('seo_score_desc')
    await tab2.close()
  })

  test('browser back restores the previous filter state', async ({ page }) => {
    await login(page)
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    // Capture initial URL after hydration so we know what "back to start" looks like.
    await page.waitForTimeout(400)
    const startUrl = page.url()

    await page.selectOption('#filter-segment', TARGET_SEGMENT)
    await page.waitForTimeout(400)
    const filterUrl = page.url()
    expect(filterUrl).not.toBe(startUrl)
    expect(new URL(filterUrl).searchParams.get('segment')).toBe(TARGET_SEGMENT)

    // Browser back — URL reverts AND the select snaps back to "all".
    await page.goBack()
    await page.waitForTimeout(400)
    expect(new URL(page.url()).searchParams.get('segment')).toBeNull()
    await expect(page.locator('#filter-segment')).toHaveValue('all')
  })
})
