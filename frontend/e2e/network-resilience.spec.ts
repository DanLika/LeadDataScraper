import { test, expect, type Page } from '@playwright/test'

// Network-resilience suite. Uses page.route() and CDP-backed
// context.setOffline() to simulate flaky backends and bad networks. Pins
// the six failure modes the dashboard must survive:
//   1. 500 from /leads → error toast, no white screen
//   2. 401 from /api/proxy/* → redirect to /login?next=
//   3. Offline → banner, mutations queued, queue drains on reconnect
//   4. Slow 3G → loading-skeleton visible, UI not frozen
//   5. Malformed JSON from backend → graceful error, no white screen
//   6. Mid-request abort (user navigates away) → no console errors
//
// Required env: E2E_BASE_URL, E2E_EMAIL, E2E_PASSWORD.

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
test.skip(!EMAIL || !PASSWORD, 'E2E_EMAIL and E2E_PASSWORD must be set')

async function login(page: Page) {
  await page.goto('/login')
  await page.fill('input[name="email"]', EMAIL)
  await page.fill('input[name="password"]', PASSWORD)
  await Promise.all([
    page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15_000 }),
    page.click('button[type="submit"]'),
  ])
}

async function captureConsoleErrors(page: Page): Promise<string[]> {
  const errors: string[] = []
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      const text = msg.text()
      // Source-map noise from the dev/prod build pipeline isn't a runtime
      // resilience failure.
      if (/Source map|sourcemap/i.test(text)) return
      errors.push(text)
    }
  })
  page.on('pageerror', (err) => errors.push(`pageerror: ${err.message}`))
  return errors
}

test.describe('network resilience', () => {
  test('1) /leads returns 500 → error toast, no white screen', async ({ page }) => {
    await login(page)
    await page.route('**/api/proxy/leads*', async (route) => {
      if (route.request().method() !== 'GET') return route.continue()
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'Database not connected' }),
      })
    })
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    // The dashboard chrome must still render (sidebar, header, hamburger
    // on mobile widths). White-screen failure = body has effectively no
    // text content. Asserting >100 chars is a coarse but reliable signal.
    const bodyTextLen = await page.evaluate(() => document.body.innerText.length)
    expect(bodyTextLen, 'white screen — body has no text').toBeGreaterThan(100)

    // Error toast surfaces the failure to the operator.
    const toast = page.locator('.toast-error, .toast').filter({ hasText: /failed|error|reach|unreachable/i })
    await expect(toast.first()).toBeVisible({ timeout: 8_000 })

    await page.unroute('**/api/proxy/leads*')
  })

  test('2) 401 from proxy → redirect to /login?next=', async ({ page }) => {
    await login(page)
    // Intercept the first /leads call after we land on /
    await page.route('**/api/proxy/leads*', (route) =>
      route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'unauthorized' }),
      }),
    )
    await page.goto('/')
    await page.waitForURL(/\/login(\?|$)/, { timeout: 10_000 })
    expect(page.url()).toMatch(/\/login\?next=/)
    expect(decodeURIComponent(page.url())).toContain('next=/')

    await page.unroute('**/api/proxy/leads*')
  })

  test('3) offline → banner shown, mutation queued, drained on reconnect', async ({ page, context }) => {
    // Stub the discovery endpoint so the queue-drain replay doesn't actually
    // kick off a real Google-Maps scrape. We're testing the queue mechanism,
    // not the upstream backend.
    await page.route('**/api/proxy/discovery/start', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ job_id: 'e2e-stub-job', status: 'starting' }),
      }),
    )
    await login(page)
    // Open the Discovery modal BEFORE going offline so the navigation
    // itself doesn't have to traverse a dead network.
    await page.goto('/?openDiscovery=1')
    await page.waitForLoadState('networkidle')
    await page.fill('#discovery-query', 'offline-test')
    await page.fill('#discovery-location', 'Mostar')

    // Flip the context offline — Playwright reports navigator.onLine=false
    // and any new request fails with ERR_INTERNET_DISCONNECTED.
    await context.setOffline(true)
    await page.evaluate(() => window.dispatchEvent(new Event('offline')))

    const banner = page.locator('[data-testid="offline-banner"]')
    await expect(banner).toBeVisible({ timeout: 5_000 })
    await expect(banner).toContainText(/offline/i)

    // The submit hits apiFetch, which sees navigator.onLine=false and
    // routes the POST into offlineQueue instead of fetch().
    await page.getByRole('button', { name: /Start Deep Search/i }).click()

    // Banner reflects the queued count (n ≥ 1).
    await expect(banner).toContainText(/\d+ action/i, { timeout: 3_000 })

    // Come back online — banner switches to "Reconnected — retrying…"
    // briefly, then disappears as the queue drains.
    await context.setOffline(false)
    await page.evaluate(() => window.dispatchEvent(new Event('online')))

    await expect(banner).toBeHidden({ timeout: 15_000 })
  })

  test('4) slow 3G → loading-skeleton visible, UI does not freeze', async ({ page }) => {
    await login(page)
    // Delay /leads by 1.5s — long enough for the skeleton to render and
    // be observable before the data lands.
    await page.route('**/api/proxy/leads*', async (route) => {
      if (route.request().method() !== 'GET') return route.continue()
      await new Promise((r) => setTimeout(r, 1500))
      await route.continue()
    })

    await page.goto('/')
    // The skeleton (data-testid added in page.tsx) must appear inside the
    // slow window. We don't use waitForLoadState here — the network isn't
    // idle yet, that's the whole point.
    const skeleton = page.locator('[data-testid="loading-skeleton"]')
    await expect(skeleton).toBeVisible({ timeout: 1_000 })

    // UI not frozen: a click on a UI element (e.g. opening the hamburger
    // menu on mobile, or any always-rendered button) must respond. The
    // header has the Settings gear in the sidebar; we use the body keydown
    // route instead — pressing Tab must move focus.
    const focusBefore = await page.evaluate(() => document.activeElement?.tagName || '')
    await page.keyboard.press('Tab')
    const focusAfter = await page.evaluate(() => document.activeElement?.tagName || '')
    expect(focusAfter, 'Tab must change focus even while a network request is pending').not.toBe(focusBefore)

    // Eventually skeleton goes away (data arrived).
    await expect(skeleton).toBeHidden({ timeout: 10_000 })
    await page.unroute('**/api/proxy/leads*')
  })

  test('5) backend returns malformed JSON → graceful error, no white screen', async ({ page }) => {
    const errors = await captureConsoleErrors(page)
    await login(page)
    await page.route('**/api/proxy/leads*', async (route) => {
      if (route.request().method() !== 'GET') return route.continue()
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: '{"leads": [{"unique_key": "bad", "name":', // truncated JSON
      })
    })
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    // No white screen.
    const bodyTextLen = await page.evaluate(() => document.body.innerText.length)
    expect(bodyTextLen).toBeGreaterThan(100)

    // The fetchLeads catch block must have surfaced *something* — either
    // a toast or a fallback empty state. The cardinal sin is an uncaught
    // exception that React's error boundary doesn't catch. Allow JSON
    // SyntaxError in the console as long as it didn't leave the app dead.
    const fatal = errors.filter((e) => /pageerror|Cannot read|undefined is not/i.test(e))
    expect(fatal, `fatal errors:\n${fatal.join('\n')}`).toEqual([])

    await page.unroute('**/api/proxy/leads*')
  })

  test('6) mid-request abort (nav away) → no console errors', async ({ page }) => {
    const errors = await captureConsoleErrors(page)
    await login(page)
    // Long delay so we navigate away before the response lands.
    await page.route('**/api/proxy/leads*', async (route) => {
      if (route.request().method() !== 'GET') return route.continue()
      await new Promise((r) => setTimeout(r, 6_000))
      await route.continue()
    })
    // Start nav to /
    const nav = page.goto('/', { waitUntil: 'commit' })
    // Immediately navigate away — fetchLeads()'s AbortSignal must clean
    // up without throwing an unhandled rejection or surfacing a console
    // error.
    await page.waitForTimeout(300)
    await page.goto('/insights')
    await page.waitForLoadState('networkidle')
    // Drain the original nav promise so we don't leak it.
    await nav.catch(() => undefined)

    // Acceptable: cancelled fetch logs (some WebKit builds emit them as
    // warnings). Fatal: unhandled promise rejection / TypeError.
    const fatal = errors.filter((e) =>
      /Unhandled|TypeError|pageerror|Cannot read properties of undefined/i.test(e),
    )
    expect(fatal, `mid-abort console fatals:\n${fatal.join('\n')}`).toEqual([])

    await page.unroute('**/api/proxy/leads*')
  })
})
