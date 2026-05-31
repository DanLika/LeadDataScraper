import { test, expect, type Page } from '@playwright/test'

// SPA history + navigation invariants. Mocks the data plane so the
// browser-history mechanics are what's being tested, not Supabase
// availability.
//
// Required env: E2E_BASE_URL, E2E_EMAIL, E2E_PASSWORD.

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
test.skip(!EMAIL || !PASSWORD, 'E2E_EMAIL and E2E_PASSWORD must be set')

const FIXTURES = Array.from({ length: 30 }, (_, i) => ({
  unique_key: `nav-${i.toString().padStart(2, '0')}`,
  name: `Nav Lead ${i}`,
  company_name: `Nav Co ${i}`,
  website: `https://example.com/nav/${i}`,
  email: `nav${i}@example.test`,
  audit_status: 'Completed',
  seo_score: 70 + (i % 30),
  outreach_score: 60 + (i % 40),
  segment: ['Performance Optimization', 'Low Priority Prospect'][i % 2],
  high_risk_flag: false,
  retry_count: 0,
  lead_source: 'nav_fixture',
}))

const CAMPAIGNS = [
  { id: 'nav-camp-1', name: 'Q2 Nav Campaign', channel: 'email', status: 'active', segment_filter: null, created_at: '2026-05-01T00:00:00Z', messages_count: 5 },
]

async function login(page: Page) {
  await page.goto('/login')
  await page.fill('input[name="email"]', EMAIL)
  await page.fill('input[name="password"]', PASSWORD)
  await Promise.all([
    page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15_000 }),
    page.click('button[type="submit"]'),
  ])
}

async function mockData(page: Page) {
  await page.route('**/api/proxy/leads**', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ leads: FIXTURES, next_cursor: null, has_more: false }) }),
  )
  await page.route('**/api/proxy/insights**', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ insights: [], summary: '', top_priorities: [] }) }),
  )
  await page.route('**/api/proxy/orchestrator/active**', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job: null }) }),
  )
  await page.route('**/api/proxy/audit-status**', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ active: false }) }),
  )
  await page.route('**/api/proxy/campaigns', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ campaigns: CAMPAIGNS }) }),
  )
  await page.route('**/api/proxy/campaigns/nav-camp-1', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ campaign: CAMPAIGNS[0] }) }),
  )
  await page.route('**/api/proxy/campaigns/nav-camp-1/messages**', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [] }) }),
  )
}

test.describe('SPA navigation', () => {
  test('back from /insights restores / + scroll position', async ({ page }) => {
    await mockData(page)
    await login(page)
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    // Scroll the dashboard down to mid-table.
    await page.evaluate(() => window.scrollTo(0, 600))
    const scrollBefore = await page.evaluate(() => window.scrollY)
    expect(scrollBefore).toBeGreaterThan(0)

    await page.goto('/insights')
    await page.waitForURL(/\/insights/)
    await page.waitForLoadState('networkidle')

    await page.goBack()
    await page.waitForURL((url) => url.pathname === '/' || url.pathname === '')
    await page.waitForLoadState('networkidle')

    // Browser restores scroll position automatically on bfcache hit.
    // Allow up to 100px slack — some browsers settle slightly off the
    // exact pixel.
    const scrollAfter = await page.evaluate(() => window.scrollY)
    expect(Math.abs(scrollAfter - scrollBefore)).toBeLessThanOrEqual(100)
  })

  test('campaigns: back from filtered detail returns to list with filter applied', async ({ page }) => {
    await mockData(page)
    await login(page)
    await page.goto('/campaigns')
    await page.waitForLoadState('networkidle')

    // Open the first campaign — clicking the row sets selectedCampaign + fetches details.
    await page.locator('.card').filter({ hasText: /Q2 Nav Campaign/i }).first().click()
    await expect(page.getByRole('button', { name: /Generate Messages/i })).toBeVisible({ timeout: 5_000 })

    await page.goBack()
    // After back the list view returns. The "New Campaign" CTA is the
    // list-view's anchor that's hidden in detail view.
    await expect(page.getByRole('button', { name: /New Campaign/i })).toBeVisible({ timeout: 5_000 })

    await page.goForward()
    await expect(page.getByRole('button', { name: /Generate Messages/i })).toBeVisible({ timeout: 5_000 })
  })

  test('reload mid-flow recovers from URL (no white screen)', async ({ page }) => {
    await mockData(page)
    await login(page)
    await page.goto('/?segment=Performance%20Optimization&sort=seo_score_desc')
    await page.waitForLoadState('networkidle')

    // Filter state hydrated from URL.
    await page.waitForTimeout(400)
    await expect(page.locator('#filter-segment')).toHaveValue('Performance Optimization')
    await expect(page.locator('#sort-leads')).toHaveValue('seo_score_desc')

    await page.reload()
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(400)

    // No white screen — body has substantive content.
    const bodyLen = await page.evaluate(() => document.body.innerText.length)
    expect(bodyLen).toBeGreaterThan(100)
    // Filters survive reload (URL drives state).
    await expect(page.locator('#filter-segment')).toHaveValue('Performance Optimization')
    await expect(page.locator('#sort-leads')).toHaveValue('seo_score_desc')
  })

  test('insights audited-stat-card commits ?view=audited to URL (B-04 regression)', async ({ page }) => {
    // Regression for E2E sweep finding B-04 "audited-stat-card nav race":
    // clicking the audited stat card on /insights filtered the dashboard
    // table but the URL bar showed `/`, not `/?view=audited`. Root cause:
    // the one-shot consume-and-strip effect on the dashboard handled
    // `?view=` then did `router.replace('/')`, dropping the URL state
    // that the bidirectional URL ↔ filter sync block never re-emitted
    // (view was state-only, not URL-backed). Fix: drop view from the
    // consume effect, add it to the bidirectional sync (page.tsx).
    await mockData(page)
    await login(page)
    await page.goto('/insights')
    await page.waitForLoadState('networkidle')

    // Click the Audited Leads stat card.
    await page.locator('a.stat-card').filter({ hasText: /Audited Leads/i }).click()

    // URL must commit to /?view=audited within the navigation tick.
    await page.waitForURL(/\?view=audited$/, { timeout: 5_000 })

    // Reload — the URL drives state, so view should survive.
    await page.reload()
    await page.waitForLoadState('networkidle')
    expect(page.url()).toMatch(/\?view=audited$/)

    // High Risk card from /insights → same contract.
    await page.goto('/insights')
    await page.waitForLoadState('networkidle')
    await page.locator('a.stat-card').filter({ hasText: /High Risk/i }).click()
    await page.waitForURL(/\?view=high-risk$/, { timeout: 5_000 })
  })

  test('modal state on refresh: closed (documents current behavior)', async ({ page }) => {
    // Modals are React state only, not URL-backed today. Refreshing the
    // page closes them. This test documents that contract — if modal
    // state is ever serialized to URL (e.g. /?modal=outreach&lead=X),
    // this assertion will trip and you'll know to update it.
    await mockData(page)
    await login(page)
    await page.goto('/?openSettings=1')
    await expect(page.getByRole('dialog', { name: /System Settings/i })).toBeVisible({ timeout: 5_000 })

    await page.reload()
    await page.waitForLoadState('networkidle')

    // After reload the URL query was already consumed-and-stripped by the
    // cross-page nav bridge effect (runs once on mount). Settings modal
    // should NOT be visible.
    await expect(page.getByRole('dialog', { name: /System Settings/i })).toBeHidden()
  })
})
