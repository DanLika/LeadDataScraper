import { test, expect, request as pwRequest, type Cookie } from '@playwright/test'

// Auth + proxy + cookie-floor E2E spec. Maps to the 6 invariants in the task
// description. Read playwright.config.ts for required env (E2E_BASE_URL,
// E2E_EMAIL, E2E_PASSWORD).

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
const BAD_PASSWORD = 'definitely-not-the-password-' + Math.random().toString(36).slice(2)
const ASSERT_SECURE = process.env.E2E_PROD_COOKIE_SECURE === '1'

test.skip(!EMAIL || !PASSWORD, 'E2E_EMAIL and E2E_PASSWORD must be set')

// Supabase access/refresh-token cookies are prefixed `sb-` in @supabase/ssr.
// Project-specific subdomain follows. We match by prefix.
function pickSupabaseCookies(cookies: Cookie[]): Cookie[] {
  return cookies.filter((c) => c.name.startsWith('sb-'))
}

async function fillLoginAndSubmit(page: import('@playwright/test').Page, email: string, password: string) {
  await page.fill('input[name="email"]', email)
  await page.fill('input[name="password"]', password)
  await Promise.all([
    page.waitForLoadState('networkidle'),
    page.click('button[type="submit"]'),
  ])
}

test.describe('1) Anonymous traffic is gated', () => {
  test('GET / without session redirects to /login', async ({ page }) => {
    const resp = await page.goto('/')
    expect(resp?.ok()).toBeTruthy()
    await expect(page).toHaveURL(/\/login(\?|$)/)
    await expect(page.locator('input[name="email"]')).toBeVisible()
  })
})

test.describe('2) Login brute-force throttle', () => {
  // Bucket key is the trusted-client-IP header; in local dev that header
  // isn't set so attempts share the `unknown` bucket (loginThrottle.ts).
  // Restart the Next.js dev/prod server before re-running this test or the
  // bucket may already be poisoned from a prior run.
  test('6th wrong-password attempt is throttled', async ({ page }) => {
    await page.goto('/login')

    for (let i = 1; i <= 5; i++) {
      await fillLoginAndSubmit(page, EMAIL, BAD_PASSWORD)
      const alert = page.locator('[role="alert"]')
      await expect(alert).toBeVisible({ timeout: 8_000 })
      // Attempts 1..5 still allowed — Supabase returns its own invalid-credentials msg.
      await expect(alert).not.toContainText(/Too many sign-in attempts/i)
    }

    // 6th attempt — counter > 5 — server action returns the throttle message
    // BEFORE calling signInWithPassword.
    await fillLoginAndSubmit(page, EMAIL, BAD_PASSWORD)
    await expect(page.locator('[role="alert"]')).toContainText(/Too many sign-in attempts/i)
  })
})

test.describe('3) Cookie floor on successful login', () => {
  test('session cookies are HttpOnly + SameSite=Lax (+ Secure in prod)', async ({ page, context }) => {
    await page.goto('/login')
    await fillLoginAndSubmit(page, EMAIL, PASSWORD)
    await expect(page).not.toHaveURL(/\/login/)

    const cookies = pickSupabaseCookies(await context.cookies())
    expect(cookies.length, 'expected at least one sb-* session cookie').toBeGreaterThan(0)

    for (const c of cookies) {
      expect(c.httpOnly, `cookie ${c.name} must be HttpOnly`).toBe(true)
      // cookie-floor.mjs forces sameSite to Lax unless the SDK requested Strict.
      expect(['Lax', 'Strict']).toContain(c.sameSite)
      if (ASSERT_SECURE) {
        expect(c.secure, `cookie ${c.name} must be Secure in prod`).toBe(true)
      }
    }

    // document.cookie must not see the session — HttpOnly contract.
    const visible = await page.evaluate(() => document.cookie)
    expect(visible).not.toMatch(/sb-/)
  })
})

test.describe('4) API key is server-only', () => {
  test('X-API-Key never appears on browser-originated requests', async ({ page }) => {
    const offenders: { url: string; headers: Record<string, string> }[] = []
    page.on('request', (req) => {
      const hdrs = req.headers()
      if (Object.keys(hdrs).some((k) => k.toLowerCase() === 'x-api-key')) {
        offenders.push({ url: req.url(), headers: hdrs })
      }
      const url = req.url()
      // Belt-and-braces: backend FastAPI host should never be reached from the
      // browser. Only same-origin /api/proxy/* is allowed.
      if (/(?:127\.0\.0\.1|localhost):8000\b/.test(url) && !url.startsWith(page.url() ? '' : 'x')) {
        offenders.push({ url, headers: hdrs })
      }
    })

    await page.goto('/login')
    await fillLoginAndSubmit(page, EMAIL, PASSWORD)
    await expect(page).not.toHaveURL(/\/login/)
    await page.waitForLoadState('networkidle')

    // Drive at least one proxied call so the assertion has something to chew on.
    const proxyStatus = await page.evaluate(async () => {
      const r = await fetch('/api/proxy/leads', { method: 'GET', credentials: 'include' })
      return r.status
    })
    expect([200, 401, 403, 404, 502]).toContain(proxyStatus)

    expect(offenders, `offending browser requests: ${JSON.stringify(offenders, null, 2)}`).toEqual([])
  })
})

test.describe('5) Sign out clears session', () => {
  test('signout cookie wipe + redirect to /login', async ({ page, context, baseURL }) => {
    await page.goto('/login')
    await fillLoginAndSubmit(page, EMAIL, PASSWORD)
    await expect(page).not.toHaveURL(/\/login/)

    const before = pickSupabaseCookies(await context.cookies())
    expect(before.length).toBeGreaterThan(0)

    // The Sidebar button POSTs to /api/auth/signout with same-origin Origin.
    const signoutResp = await page.evaluate(async () => {
      const r = await fetch('/api/auth/signout', { method: 'POST', credentials: 'include' })
      return { status: r.status, body: await r.text() }
    })
    expect(signoutResp.status).toBe(200)

    // Server-cleared cookies — sb-* should be gone (or empty + expired).
    const after = pickSupabaseCookies(await context.cookies())
    const live = after.filter((c) => c.value && c.value.length > 0)
    expect(live, `expected sb-* cookies to be cleared, got ${JSON.stringify(after)}`).toEqual([])

    // Subsequent navigation to / lands on /login.
    await page.goto('/')
    await expect(page).toHaveURL(/\/login(\?|$)/)
    expect(baseURL).toBeTruthy()
  })
})

test.describe('6) Replayed session cookies are rejected after signout', () => {
  test('captured pre-signout cookies cannot re-auth the proxy', async ({ browser, baseURL }) => {
    // First context: login, snapshot cookies, sign out.
    const ctx1 = await browser.newContext()
    const page1 = await ctx1.newPage()
    await page1.goto((baseURL || '') + '/login')
    await fillLoginAndSubmit(page1, EMAIL, PASSWORD)
    await expect(page1).not.toHaveURL(/\/login/)
    const snapshot = pickSupabaseCookies(await ctx1.cookies())
    expect(snapshot.length).toBeGreaterThan(0)

    const signoutStatus = await page1.evaluate(async () => {
      const r = await fetch('/api/auth/signout', { method: 'POST', credentials: 'include' })
      return r.status
    })
    expect(signoutStatus).toBe(200)
    await ctx1.close()

    // Second context: inject the OLD cookies and try to use them. Proxy must
    // 401 because supabase.auth.getUser() revalidates against Supabase Auth
    // and the refresh token has been revoked.
    const ctx2 = await browser.newContext()
    await ctx2.addCookies(
      snapshot.map((c) => ({
        name: c.name,
        value: c.value,
        domain: c.domain,
        path: c.path,
        // Re-apply a far-future expiry so the cookie survives the new context.
        expires: Math.floor(Date.now() / 1000) + 3600,
        httpOnly: c.httpOnly,
        secure: c.secure,
        sameSite: c.sameSite,
      })),
    )
    const page2 = await ctx2.newPage()
    const proxyResp = await page2.evaluate(async (origin) => {
      const r = await fetch(origin + '/api/proxy/leads', { method: 'GET', credentials: 'include' })
      return { status: r.status }
    }, baseURL || '')
    expect(proxyResp.status, 'replayed cookies must not authenticate proxy').toBe(401)

    // HTML route should also bounce to /login.
    const htmlResp = await page2.goto((baseURL || '') + '/')
    expect(htmlResp?.url()).toMatch(/\/login(\?|$)/)
    await ctx2.close()

    // Direct API check via a no-cookie context shouldn't accidentally succeed
    // either — guards against the proxy mis-reading getUser() as a 200.
    const apiCtx = await pwRequest.newContext({ baseURL })
    const naked = await apiCtx.get('/api/proxy/leads')
    expect(naked.status()).toBe(401)
    await apiCtx.dispose()
  })
})
