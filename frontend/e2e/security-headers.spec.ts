import { test, expect, type Page } from '@playwright/test'

// Verifies the prod browser-security headers configured in
// `frontend/next.config.ts` actually land on the authed dashboard. Run
// against `next build && next start` — `next dev` ships `'unsafe-inline'`
// / `'unsafe-eval'` in script-src and this spec will (correctly) fail.
//
// Required env:
//   E2E_BASE_URL   default http://localhost:3000 — point at the prod-mode build
//   E2E_EMAIL, E2E_PASSWORD  Supabase Auth operator
//   E2E_PROD=1     set when serving over real HTTPS — gates the
//                  Strict-Transport-Security assertion. HSTS is configured
//                  in next.config.ts unconditionally but only meaningful
//                  on HTTPS; un-gated, an HTTPS-less local prod-build
//                  would pass the header check while serving cleartext.

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
const ASSERT_HSTS = process.env.E2E_PROD === '1'

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

function parseCsp(value: string): Map<string, string[]> {
  const directives = new Map<string, string[]>()
  for (const part of value.split(';')) {
    const trimmed = part.trim()
    if (!trimmed) continue
    const [name, ...sources] = trimmed.split(/\s+/)
    directives.set(name.toLowerCase(), sources)
  }
  return directives
}

function parseHsts(value: string): { maxAge: number; includeSubDomains: boolean; preload: boolean } {
  const parts = value.split(';').map((p) => p.trim().toLowerCase())
  let maxAge = 0
  for (const p of parts) {
    if (p.startsWith('max-age=')) maxAge = Number(p.slice('max-age='.length)) || 0
  }
  return {
    maxAge,
    includeSubDomains: parts.includes('includesubdomains'),
    preload: parts.includes('preload'),
  }
}

test('prod-mode dashboard: security headers + clean console + no mixed content', async ({ page }) => {
  // Mixed-content detection: any subresource (script/img/css/font/xhr) whose
  // URL starts with http:// while the document is loaded over https:// is a
  // mixed-content surface. Track from page.on('request') so we see redirects
  // and dynamically-injected resources too.
  const mixedContent: { url: string; resourceType: string }[] = []
  const consoleErrors: { text: string; location: string }[] = []
  const pageErrors: string[] = []

  page.on('request', (req) => {
    const url = req.url()
    if (url.startsWith('http://') && page.url().startsWith('https://')) {
      mixedContent.push({ url, resourceType: req.resourceType() })
    }
  })
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      // Filter out source-map-not-found noise that Next emits in some setups
      // — that's a build artifact, not a runtime security issue.
      const text = msg.text()
      if (/Source map|sourcemap/i.test(text)) return
      const loc = msg.location()
      consoleErrors.push({ text, location: `${loc.url}:${loc.lineNumber}` })
    }
  })
  page.on('pageerror', (err) => pageErrors.push(err.message))

  await login(page)

  // page.goto returns the main-document response — the one whose headers
  // carry the next.config.ts security stack.
  const navResp = await page.goto('/')
  expect(navResp, 'must have a dashboard HTML response to inspect').toBeTruthy()
  expect(navResp!.status(), `dashboard must load 200, got ${navResp!.status()}`).toBeLessThan(400)

  const rawHeaders = navResp!.headers()
  const headers: Record<string, string> = {}
  for (const k of Object.keys(rawHeaders)) {
    headers[k.toLowerCase()] = rawHeaders[k]
  }

  const missing: string[] = []
  const required = [
    'content-security-policy',
    'x-frame-options',
    'x-content-type-options',
    'referrer-policy',
  ]
  if (ASSERT_HSTS) required.push('strict-transport-security')
  for (const name of required) {
    if (!headers[name]) missing.push(name)
  }
  expect(missing, `missing security headers: ${missing.join(', ')}`).toEqual([])

  // ---- CSP: script-src 'self', no 'unsafe-inline' / 'unsafe-eval' in prod ----
  const csp = parseCsp(headers['content-security-policy'])
  const scriptSrc = csp.get('script-src') || []
  expect(scriptSrc, `script-src directive must exist`).not.toEqual([])
  expect(scriptSrc, `script-src must include 'self'`).toContain("'self'")
  expect(
    scriptSrc,
    `script-src must NOT include 'unsafe-inline' in prod (got ${scriptSrc.join(' ')})`,
  ).not.toContain("'unsafe-inline'")
  expect(
    scriptSrc,
    `script-src must NOT include 'unsafe-eval' in prod (got ${scriptSrc.join(' ')})`,
  ).not.toContain("'unsafe-eval'")

  // ---- HSTS: max-age >= 2y, includeSubDomains, preload ----
  if (ASSERT_HSTS) {
    const hsts = parseHsts(headers['strict-transport-security'])
    expect(hsts.maxAge, `HSTS max-age must be ≥ 63072000s (2y), got ${hsts.maxAge}`).toBeGreaterThanOrEqual(63072000)
    expect(hsts.includeSubDomains, 'HSTS must declare includeSubDomains').toBe(true)
    expect(hsts.preload, 'HSTS must declare preload').toBe(true)
  }

  // ---- X-Frame-Options: DENY ----
  expect(headers['x-frame-options']?.toUpperCase()).toBe('DENY')

  // ---- X-Content-Type-Options: nosniff ----
  expect(headers['x-content-type-options']?.toLowerCase()).toBe('nosniff')

  // ---- Referrer-Policy: set (any value — match the spirit of CLAUDE.md) ----
  expect(headers['referrer-policy']?.length ?? 0, 'Referrer-Policy must be non-empty').toBeGreaterThan(0)

  // Let any deferred subresources / hydration errors land before we count.
  await page.waitForLoadState('networkidle')

  // ---- No console errors / page errors ----
  expect(
    consoleErrors,
    `console errors during dashboard load:\n${consoleErrors.map((e) => `  ${e.location}  ${e.text}`).join('\n')}`,
  ).toEqual([])
  expect(
    pageErrors,
    `uncaught page errors:\n${pageErrors.map((m) => `  ${m}`).join('\n')}`,
  ).toEqual([])

  // ---- No mixed content ----
  expect(
    mixedContent,
    `mixed-content requests on HTTPS dashboard:\n${mixedContent.map((m) => `  [${m.resourceType}] ${m.url}`).join('\n')}`,
  ).toEqual([])
})
