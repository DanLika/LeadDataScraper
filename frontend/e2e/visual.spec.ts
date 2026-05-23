import { test, expect, type Page } from '@playwright/test'

// Visual regression baselines. All upstream API responses are mocked so
// the screenshots only reflect frontend rendering — not Supabase data
// drift. Baselines live next to this file in e2e/visual.spec.ts-snapshots/.
// Update intentionally via
// `npm run e2e -- --update-snapshots e2e/visual.spec.ts`.
//
// maxDiffPixelRatio: 0.01 (≤1% pixels may differ) covers anti-aliasing
// and font-rendering jitter without masking real layout regressions.
//
// **macOS-only by design.** Baselines are pixel-locked to macOS chromium
// (Inter→SF Pro fallback). Linux runners render via DejaVu/Liberation
// which blows past the 1% diff budget on body text alone. CI ubuntu-
// latest auto-skips this file via the platform guard below; visual
// regression is a LOCAL contract — dev runs `npm run e2e` on macOS,
// inspects the diff in __snapshots__, regenerates with --update-snapshots,
// commits the PNGs. See README in e2e/visual.spec.ts-snapshots/.
//
// Required env: E2E_BASE_URL, E2E_EMAIL, E2E_PASSWORD.

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
test.skip(!EMAIL || !PASSWORD, 'E2E_EMAIL and E2E_PASSWORD must be set')
test.skip(
  process.platform !== 'darwin',
  'visual baselines are pixel-locked to macOS chromium; CI Linux runs would diff on font fallback',
)

const SNAP_OPTS = {
  maxDiffPixelRatio: 0.01,
  animations: 'disabled' as const,
  caret: 'hide' as const,
}

const FIXTURE_LEADS_20 = Array.from({ length: 20 }, (_, i) => ({
  unique_key: `vis-${i.toString().padStart(2, '0')}`,
  name: `Fixture Lead ${i.toString().padStart(2, '0')}`,
  company_name: `Fixture Co ${i.toString().padStart(2, '0')}`,
  website: `https://example.com/vis/${i}`,
  email: `vis${i}@example.test`,
  phone: `+1-555-${(1000 + i).toString()}`,
  audit_status: i % 3 === 0 ? 'Completed' : i % 3 === 1 ? 'Pending' : 'Failed',
  seo_score: i % 3 === 0 ? 50 + i * 2 : null,
  outreach_score: i % 3 === 0 ? 40 + i * 2 : null,
  segment: ['Performance Optimization', 'Low Priority Prospect', 'High Conversion Target'][i % 3],
  high_risk_flag: i % 5 === 0,
  retry_count: 0,
  lead_source: 'visual_fixture',
  email_hook: `Quick SEO win for Fixture Co ${i.toString().padStart(2, '0')}`,
  created_at: `2026-05-01T00:00:${(i % 60).toString().padStart(2, '0')}Z`,
}))

async function login(page: Page) {
  await page.goto('/login')
  await page.fill('input[name="email"]', EMAIL)
  await page.fill('input[name="password"]', PASSWORD)
  await Promise.all([
    page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15_000 }),
    page.click('button[type="submit"]'),
  ])
}

async function mockDashboardEmpty(page: Page) {
  await page.route('**/api/proxy/leads**', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ leads: [], next_cursor: null, has_more: false }) }),
  )
  await page.route('**/api/proxy/insights**', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ insights: [], summary: 'No leads yet.', top_priorities: [] }) }),
  )
  await page.route('**/api/proxy/orchestrator/active**', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job: null }) }),
  )
  await page.route('**/api/proxy/audit-status**', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ active: false }) }),
  )
  // /insights page reads stats via this route. Without the mock, the fetch
  // falls through to a real backend (if uvicorn is up locally) and the
  // baseline becomes non-deterministic across machines.
  await page.route('**/api/proxy/stats**', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ total_leads: 0, audit_status_distribution: [], seo_score_ranges: [], source_distribution: [] }) }),
  )
}

async function mockDashboardPopulated(page: Page) {
  await page.route('**/api/proxy/leads**', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ leads: FIXTURE_LEADS_20, next_cursor: null, has_more: false }) }),
  )
  await page.route('**/api/proxy/insights**', (r) =>
    r.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        insights: ['10 healthy leads', '4 high-risk', '6 unaudited'],
        summary: '20 leads — focus on Performance Optimization segment.',
        top_priorities: [
          { name: 'Fixture Co 00', reason: 'High outreach score' },
          { name: 'Fixture Co 03', reason: 'Recently audited' },
        ],
      }),
    }),
  )
  await page.route('**/api/proxy/orchestrator/active**', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job: null }) }),
  )
  await page.route('**/api/proxy/audit-status**', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ active: false }) }),
  )
  // Numbers match FIXTURE_LEADS_20:
  //   Completed (i%3==0): 7 leads, SEO scores 50/56/62/68/74/80/86
  //   Pending   (i%3==1): 7 leads
  //   Failed    (i%3==2): 6 leads
  await page.route('**/api/proxy/stats**', (r) =>
    r.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        total_leads: 20,
        audit_status_distribution: [
          { name: 'Completed', value: 7 },
          { name: 'Pending', value: 7 },
          { name: 'Failed', value: 6 },
        ],
        seo_score_ranges: [
          { range: '0-20', count: 0 },
          { range: '21-40', count: 0 },
          { range: '41-60', count: 2 },
          { range: '61-80', count: 4 },
          { range: '81-100', count: 1 },
        ],
        source_distribution: [{ name: 'visual_fixture', value: 20 }],
      }),
    }),
  )
}

async function settleForSnapshot(page: Page) {
  await page.waitForLoadState('networkidle')
  // Belt-and-braces: kill any lingering CSS-driven motion the screenshot
  // animations:'disabled' option doesn't already cover.
  await page.addStyleTag({
    content: `
      *, *::before, *::after {
        animation: none !important;
        transition: none !important;
      }
      [data-testid="offline-banner"] { display: none !important; }
    `,
  })
  await page.waitForTimeout(150)
}

test.describe('visual regression', () => {
  test('/login (empty)', async ({ page }) => {
    await page.goto('/login')
    await settleForSnapshot(page)
    await expect(page).toHaveScreenshot('login.png', SNAP_OPTS)
  })

  test('/ — empty state (0 leads)', async ({ page }) => {
    await mockDashboardEmpty(page)
    await login(page)
    await page.goto('/')
    await settleForSnapshot(page)
    await expect(page).toHaveScreenshot('dashboard-empty.png', SNAP_OPTS)
  })

  test('/ — populated state (20 fixture leads)', async ({ page }) => {
    await mockDashboardPopulated(page)
    await login(page)
    await page.goto('/')
    await settleForSnapshot(page)
    await expect(page).toHaveScreenshot('dashboard-populated.png', SNAP_OPTS)
  })

  test('/insights — with seeded data', async ({ page }) => {
    await mockDashboardPopulated(page)
    await login(page)
    await page.goto('/insights')
    await settleForSnapshot(page)
    await expect(page).toHaveScreenshot('insights.png', SNAP_OPTS)
  })

  test('/campaigns — 1 active campaign', async ({ page }) => {
    await page.route('**/api/proxy/campaigns**', (r) =>
      r.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          campaigns: [
            {
              id: 'vis-campaign-1',
              name: 'Q2 Dentists Mostar',
              channel: 'email',
              status: 'active',
              segment_filter: 'Performance Optimization',
              created_at: '2026-05-01T00:00:00Z',
              messages_count: 12,
            },
          ],
        }),
      }),
    )
    await mockDashboardPopulated(page)
    await login(page)
    await page.goto('/campaigns')
    await settleForSnapshot(page)
    await expect(page).toHaveScreenshot('campaigns.png', SNAP_OPTS)
  })

  test('lead detail (outreach) modal open', async ({ page }) => {
    await mockDashboardPopulated(page)
    // Handler reads { draft, subject, lead_email } flat off the response —
    // NOT wrapped in { result: ... }. See page.tsx::handleDraftOutreach.
    // `.first()` below picks the topmost rendered Mail button. The table
    // sorts by created_at DESC, so the newest fixture lead (Fixture Co 19)
    // wins. Mock content must reference Co 19, not Co 00, or the baseline
    // shows a confusing title-vs-body mismatch.
    await page.route('**/api/proxy/draft-outreach', (r) =>
      r.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          draft: "Hi team at Fixture Co 19,\n\nI noticed your site could use an SEO boost. Let's chat.\n\nBest,\nYour Name",
          subject: 'Quick win for Fixture Co 19 SEO',
          lead_email: 'vis19@example.test',
        }),
      }),
    )
    // Handler chains a /draft-linkedin call right after — mock it so the
    // request doesn't fall through to a real proxy round-trip.
    await page.route('**/api/proxy/draft-linkedin', (r) =>
      r.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          draft: 'Hi! Saw Fixture Co 19 could use an SEO boost — happy to share a quick teardown if useful.',
        }),
      }),
    )
    await login(page)
    await page.goto('/')
    await settleForSnapshot(page)
    // Trigger the outreach modal for the first lead.
    const draftBtn = page.getByRole('button', { name: /Draft email outreach for/i }).first()
    await draftBtn.click()
    const dialog = page.getByRole('dialog', { name: /Outreach for/i })
    await expect(dialog).toBeVisible({ timeout: 5_000 })
    await settleForSnapshot(page)
    await expect(page).toHaveScreenshot('lead-detail-modal.png', SNAP_OPTS)
  })

  test('AI plan card visible', async ({ page }) => {
    await mockDashboardPopulated(page)
    await page.route('**/api/proxy/ask', (r) =>
      r.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          response: "I'll find dentists in Sarajevo for you.",
          plan: {
            task: 'DISCOVERY_SEARCH',
            params: { query: 'dentists', location: 'Sarajevo' },
            reasoning: 'Discovery on Google Maps for dentists in Sarajevo.',
          },
        }),
      }),
    )
    await login(page)
    await page.goto('/')
    await settleForSnapshot(page)

    // Open chat, send the message.
    const opener = page.getByRole('button', { name: /Open AI chat/i })
    if (await opener.isVisible().catch(() => false)) await opener.click()
    await page.getByRole('textbox', { name: /Ask the AI assistant/i }).fill('find 3 dentists in Sarajevo')
    await page.getByRole('button', { name: /Send message/i }).click()
    await expect(page.getByTestId('plan-card')).toBeVisible({ timeout: 5_000 })
    await settleForSnapshot(page)
    await expect(page).toHaveScreenshot('ai-plan-card.png', SNAP_OPTS)
  })
})
