import { test, expect, type Page, type Route } from '@playwright/test'

// Polling contract for orchestration status. All routes mocked so the
// "backend ticked processed_count" moment is deterministic.
//
// Required env: E2E_BASE_URL, E2E_EMAIL, E2E_PASSWORD.

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
test.skip(!EMAIL || !PASSWORD, 'E2E_EMAIL and E2E_PASSWORD must be set')

const JOB_ID = 'poll-e2e-stub'
const POLL_INTERVAL_MS = 3_000 // dashboard polls /orchestrator/status/{id} at ~2-3s

async function login(page: Page) {
  await page.goto('/login')
  await page.fill('input[name="email"]', EMAIL)
  await page.fill('input[name="password"]', PASSWORD)
  await Promise.all([
    page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15_000 }),
    page.click('button[type="submit"]'),
  ])
}

// Mutable shared state — the route returns whatever this looks like at
// the moment of the request, so the test can step it forward in time.
type JobShape = { id: string; status: string; processed_count: number; total_count: number; current_phase: string; type?: string }
let jobState: JobShape

async function fulfillJson(route: Route, body: object) {
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
}

test.describe('orchestration polling', () => {
  test.beforeEach(async ({ page }) => {
    jobState = { id: JOB_ID, status: 'running', processed_count: 0, total_count: 100, current_phase: 'Processing batch (0/100)', type: 'massive' }
    await page.route('**/api/proxy/leads**', (r) => fulfillJson(r, { leads: [], next_cursor: null, has_more: false }))
    await page.route('**/api/proxy/insights**', (r) => fulfillJson(r, { insights: [], summary: '', top_priorities: [] }))
    await page.route('**/api/proxy/audit-status**', (r) => fulfillJson(r, { active: false }))
    await page.route('**/api/proxy/orchestrator/active**', (r) => fulfillJson(r, { job: jobState }))
    await page.route('**/api/proxy/orchestrator/status/**', (r) => fulfillJson(r, jobState))
  })

  test('UI updates within next poll after backend tick + completes within 1 poll', async ({ page }) => {
    await login(page)
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    // Cross-tab pickup poll runs every 5s; wait until the UI adopts our job.
    const orchestrateBtn = page.getByRole('button', { name: /AI Orchestrate/i })
    await expect(orchestrateBtn).toBeDisabled({ timeout: 10_000 })

    // Note initial state.
    const initialPhase = await page.locator(`text=Processing batch (0/100)`).first().textContent().catch(() => null)
    expect(initialPhase, 'phase string must be visible').not.toBeNull()

    // Backend ticks: 0 → 50.
    jobState.processed_count = 50
    jobState.current_phase = 'Processing batch (50/100)'
    await expect(page.locator('text=Processing batch (50/100)').first()).toBeVisible({ timeout: POLL_INTERVAL_MS + 1_500 })

    // Backend completes the job.
    jobState.processed_count = 100
    jobState.status = 'completed'
    jobState.current_phase = 'Finished'
    // The dashboard's status poller exits when status flips to
    // completed/failed; the "AI Orchestrate" button must un-disable
    // within one poll cycle.
    await expect(orchestrateBtn).toBeEnabled({ timeout: POLL_INTERVAL_MS + 1_500 })
  })

  test('backgrounded tab → polling slows; refocus → catches up', async ({ page }) => {
    // The dashboard does not currently special-case visibilitychange — it
    // polls at fixed 2–5s intervals regardless. We document that by
    // asserting refocus brings the UI to current state, NOT that the
    // hidden tab paused polling. If a future change adds Page Visibility
    // gating, the first assertion below will become the load-bearing one.
    await login(page)
    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await expect(page.getByRole('button', { name: /AI Orchestrate/i })).toBeDisabled({ timeout: 10_000 })

    // Fire visibilitychange — most modern browsers still tick setInterval
    // for foregrounded windows under DevTools control even with this event,
    // so this is a contract probe, not a clock-pause.
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' })
      document.dispatchEvent(new Event('visibilitychange'))
    })

    // While "hidden", advance the job.
    jobState.processed_count = 25
    jobState.current_phase = 'Processing batch (25/100)'
    await page.waitForTimeout(POLL_INTERVAL_MS + 500) // give the poll a chance even if hidden

    // Refocus.
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => false })
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
      document.dispatchEvent(new Event('visibilitychange'))
    })

    // After refocus the UI must reflect the advanced state within one
    // poll. This is the load-bearing assertion — whether polling paused
    // during the hidden window or not, the refocused tab must converge.
    await expect(page.locator('text=Processing batch (25/100)').first()).toBeVisible({ timeout: POLL_INTERVAL_MS + 2_000 })
  })
})
